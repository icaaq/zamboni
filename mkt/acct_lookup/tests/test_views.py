from decimal import Decimal
import json

from pyquery import PyQuery as pq
from nose.tools import eq_

from addons.models import Addon
import amo
from amo.urlresolvers import reverse
from amo.tests import TestCase, ESTestCase, app_factory
from market.models import Refund, AddonPaymentData
from mkt.webapps.models import Webapp
from stats.models import Contribution
from users.cron import reindex_users
from users.models import UserProfile


class AcctLookupTest:

    def setUp(self):
        assert self.client.login(username='support-staff@mozilla.com',
                                 password='password')


class TestAcctSummary(AcctLookupTest, TestCase):
    fixtures = ['base/users', 'webapps/337141-steamcube']

    def setUp(self):
        super(TestAcctSummary, self).setUp()
        self.user = UserProfile.objects.get(pk=31337)  # steamcube
        self.steamcube = Addon.objects.get(pk=337141)
        self.otherapp = app_factory(app_slug='otherapp')
        self.reg_user = UserProfile.objects.get(email='regular@mozilla.com')
        self.summary_url = reverse('acct_lookup.summary', args=[self.user.pk])

    def buy_stuff(self, contrib_type):
        for i in range(3):
            if i == 1:
                curr = 'GBR'
            else:
                curr = 'USD'
            amount = Decimal('2.00')
            Contribution.objects.create(addon=self.steamcube,
                                        type=contrib_type,
                                        currency=curr,
                                        amount=amount,
                                        user_id=self.user.pk)

    def summary(self, expected_status=200):
        res = self.client.get(self.summary_url)
        eq_(res.status_code, expected_status)
        return res

    def test_home_auth(self):
        self.client.logout()
        res = self.client.get(reverse('acct_lookup.home'))
        self.assertLoginRedirects(res, reverse('acct_lookup.home'))

    def test_summary_auth(self):
        self.client.logout()
        res = self.client.get(self.summary_url)
        self.assertLoginRedirects(res, self.summary_url)

    def test_home(self):
        res = self.client.get(reverse('acct_lookup.home'))
        self.assertNoFormErrors(res)
        eq_(res.status_code, 200)

    def test_basic_summary(self):
        res = self.summary()
        eq_(res.context['account'].pk, self.user.pk)

    def test_app_counts(self):
        self.buy_stuff(amo.CONTRIB_PURCHASE)
        sm = self.summary().context['app_summary']
        eq_(sm['app_total'], 3)
        eq_(sm['app_amount']['USD'], 4.0)
        eq_(sm['app_amount']['GBR'], 2.0)

    def test_inapp_counts(self):
        self.buy_stuff(amo.CONTRIB_INAPP)
        sm = self.summary().context['app_summary']
        eq_(sm['inapp_total'], 3)
        eq_(sm['inapp_amount']['USD'], 4.0)
        eq_(sm['inapp_amount']['GBR'], 2.0)

    def test_requested_refunds(self):
        contrib = Contribution.objects.create(type=amo.CONTRIB_PURCHASE,
                                              user_id=self.user.pk,
                                              addon=self.steamcube,
                                              currency='USD',
                                              amount='0.99')
        Refund.objects.create(contribution=contrib)
        res = self.summary()
        eq_(res.context['refund_summary']['requested'], 1)
        eq_(res.context['refund_summary']['approved'], 0)

    def test_approved_refunds(self):
        contrib = Contribution.objects.create(type=amo.CONTRIB_PURCHASE,
                                              user_id=self.user.pk,
                                              addon=self.steamcube,
                                              currency='USD',
                                              amount='0.99')
        Refund.objects.create(contribution=contrib,
                              status=amo.REFUND_APPROVED_INSTANT)
        res = self.summary()
        eq_(res.context['refund_summary']['requested'], 1)
        eq_(res.context['refund_summary']['approved'], 1)

    def test_app_created(self):
        res = self.summary()
        eq_(len(res.context['user_addons']), 1)

    def test_paypal_ids(self):
        self.user.addons.update(paypal_id='somedev@app.com')
        res = self.summary()
        eq_(list(res.context['paypal_ids']), [u'somedev@app.com'])

    def test_no_paypal(self):
        self.user.addons.update(paypal_id='')
        res = self.summary()
        eq_(list(res.context['paypal_ids']), [])

    def test_payment_data(self):
        AddonPaymentData.objects.create(addon=self.steamcube,
                                        full_name='Ed Peabody Jr.',
                                        business_name='Mr. Peabody',
                                        phone='(1) 773-111-2222',
                                        address_one='1111 W Leland Ave',
                                        address_two='Apt 1W',
                                        city='Chicago',
                                        post_code='60640',
                                        country='USA',
                                        state='Illinois')
        res = self.summary()
        pd = res.context['payment_data'][0]
        eq_(pd.full_name, 'Ed Peabody Jr.')
        eq_(pd.business_name, 'Mr. Peabody')
        eq_(pd.address_one, '1111 W Leland Ave')
        eq_(pd.address_two, 'Apt 1W')
        eq_(pd.city, 'Chicago')
        eq_(pd.state, 'Illinois')
        eq_(pd.post_code, '60640')
        eq_(pd.country, 'USA')

    def test_no_payment_data(self):
        res = self.summary()
        eq_(res.context['payment_data'], [])


class TestAcctSearch(AcctLookupTest, ESTestCase):
    fixtures = ['base/users']

    @classmethod
    def setUpClass(cls):
        super(TestAcctSearch, cls).setUpClass()
        reindex_users()

    def setUp(self):
        super(TestAcctSearch, self).setUp()
        self.user = UserProfile.objects.get(username='clouserw')

    def search(self, q, expect_results=True):
        res = self.client.get(reverse('acct_lookup.search'), {'q': q})
        data = json.loads(res.content)
        if expect_results:
            assert len(data['results']), 'should be more than 0 results'
        return data

    def verify_result(self, data):
        eq_(data['results'][0]['username'], self.user.username)
        eq_(data['results'][0]['display_name'], self.user.display_name)
        eq_(data['results'][0]['email'], self.user.email)
        eq_(data['results'][0]['id'], self.user.pk)
        eq_(data['results'][0]['url'], reverse('acct_lookup.summary',
                                               args=[self.user.pk]))

    def test_auth_required(self):
        self.client.logout()
        res = self.client.get(reverse('acct_lookup.search'))
        self.assertLoginRedirects(res, reverse('acct_lookup.search'))

    def test_by_username(self):
        self.user.update(username='newusername')
        self.refresh()
        data = self.search('newus')
        self.verify_result(data)

    def test_by_display_name(self):
        self.user.update(display_name='Kumar McMillan')
        self.refresh()
        data = self.search('mcmill')
        self.verify_result(data)

    def test_by_id(self):
        data = self.search(self.user.pk)
        self.verify_result(data)

    def test_by_email(self):
        self.user.update(email='fonzi@happydays.com')
        self.refresh()
        data = self.search('fonzih')
        self.verify_result(data)

    def test_no_results(self):
        data = self.search('__garbage__', expect_results=False)
        eq_(data['results'], [])


class TestPurchases(amo.tests.TestCase):
    fixtures = ['base/users', 'webapps/337141-steamcube']

    def setUp(self):
        self.app = Webapp.objects.get(pk=337141)
        self.reviewer = UserProfile.objects.get(username='admin')
        self.user = UserProfile.objects.get(pk=999)
        self.url = reverse('acct_lookup.purchases', args=[self.user.pk])

    def test_not_allowed(self):
        self.client.logout()
        self.assertLoginRequired(self.client.get(self.url))

    def test_not_even_mine(self):
        self.client.login(username=self.user.email, password='password')
        eq_(self.client.get(self.url).status_code, 403)

    def test_access(self):
        self.client.login(username=self.reviewer.email, password='password')
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        eq_(len(pq(res.content)('p.no-results')), 1)

    def test_purchase_shows_up(self):
        Contribution.objects.create(user=self.user, addon=self.app,
                                    amount=1, type=amo.CONTRIB_PURCHASE)
        self.client.login(username=self.reviewer.email, password='password')
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        doc = pq(res.content)
        eq_(doc('div.info a').attr('href'), self.app.get_detail_url())

    def test_no_support_link(self):
        for type_ in [amo.CONTRIB_PURCHASE, amo.CONTRIB_INAPP]:
            Contribution.objects.create(user=self.user, addon=self.app,
                                        amount=1, type=type_)
        self.client.login(username=self.reviewer.email, password='password')
        res = self.client.get(self.url)
        eq_(res.status_code, 200)
        doc = pq(res.content)
        eq_(len(doc('.item a.request-support')), 0)
