import os
from urllib.parse import parse_qs, urlparse

import swapper
from django.contrib.auth import SESSION_KEY, get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import reverse
from djangosaml2.tests import auth_response, conf
from djangosaml2.utils import get_session_id_from_saml2, saml2_from_httpredirect_request
from rest_framework.authtoken.models import Token

from openwisp_radius.saml.utils import get_url_or_path

from .utils import TestSamlUtilities

OrganizationUser = swapper.load_model('openwisp_users', 'OrganizationUser')
RadiusToken = swapper.load_model('openwisp_radius', 'RadiusToken')
User = get_user_model()


BASE_PATH = os.path.dirname(os.path.abspath(__file__))
METADATA_PATH = os.path.join(BASE_PATH, 'remote_idp_metadata.xml')
ATTRIBUTE_MAPS_DIR = os.path.join(BASE_PATH, 'attribute-maps')
CERT_PATH = os.path.join(BASE_PATH, 'mycert.pem')
KEY_PATH = os.path.join(BASE_PATH, 'mycert.key')


@override_settings(
    SAML_CONFIG=conf.create_conf(
        sp_host='sp.example.com',
        idp_hosts=['idp.example.com'],
        metadata_file=METADATA_PATH,
    ),
    SAML_ATTRIBUTE_MAPPING={'uid': ('username',)},
    SAML_USE_NAME_ID_AS_USERNAME=False,
)
class TestAssertionConsumerServiceView(TestSamlUtilities, TestCase):
    login_url = reverse('radius:saml2_login')

    def _get_relay_state(self, redirect_url, org_slug):
        return f'{redirect_url}?org={org_slug}'

    def _get_saml_response_for_acs_view(self, relay_state):
        response = self.client.get(self.login_url)
        saml2_req = saml2_from_httpredirect_request(response.url)
        session_id = get_session_id_from_saml2(saml2_req)
        self.add_outstanding_query(session_id, relay_state)
        return auth_response(session_id, 'org_user'), relay_state

    def _post_successful_auth_assertions(self, query_params, org_slug):
        self.assertEqual(User.objects.count(), 1)
        user_id = self.client.session[SESSION_KEY]
        user = User.objects.get(id=user_id)
        self.assertEqual(user.username, 'org_user')
        self.assertEqual(OrganizationUser.objects.count(), 1)
        org_user = OrganizationUser.objects.get(user_id=user_id)
        self.assertEqual(org_user.organization.slug, org_slug)
        expected_query_params = {
            'username': ['org_user'],
            'token': [Token.objects.get(user_id=user_id).key],
            'radius_user_token': [RadiusToken.objects.get(user_id=user_id).key],
        }
        self.assertDictEqual(query_params, expected_query_params)

    def test_organization_slug_present(self):
        expected_redirect_url = 'https://captive-portal.example.com'
        org_slug = 'default'
        relay_state = self._get_relay_state(
            redirect_url=expected_redirect_url, org_slug=org_slug
        )
        saml_response, relay_state = self._get_saml_response_for_acs_view(relay_state)
        response = self.client.post(
            reverse('radius:saml2_acs'),
            {
                'SAMLResponse': self.b64_for_post(saml_response),
                'RelayState': relay_state,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_url_or_path(response.url), expected_redirect_url)
        query_params = parse_qs(urlparse(response.url).query)
        self._post_successful_auth_assertions(query_params, org_slug)

    def test_organization_slug_absent(self):
        expected_redirect_url = 'https://captive-portal.example.com'
        org_slug = ''
        relay_state = self._get_relay_state(
            redirect_url=expected_redirect_url, org_slug=org_slug
        )
        saml_response, relay_state = self._get_saml_response_for_acs_view(relay_state)
        with self.assertRaises(ImproperlyConfigured):
            self.client.post(
                reverse('radius:saml2_acs'),
                {
                    'SAMLResponse': self.b64_for_post(saml_response),
                    'RelayState': relay_state,
                },
            )

    def test_relay_state_relative_path(self):
        expected_redirect_path = '/captive/portal/page'
        org_slug = 'default'
        relay_state = self._get_relay_state(
            redirect_url=expected_redirect_path, org_slug=org_slug
        )
        saml_response, relay_state = self._get_saml_response_for_acs_view(relay_state)
        response = self.client.post(
            reverse('radius:saml2_acs'),
            {
                'SAMLResponse': self.b64_for_post(saml_response),
                'RelayState': relay_state,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_url_or_path(response.url), expected_redirect_path)
        query_params = parse_qs(urlparse(response.url).query)
        self._post_successful_auth_assertions(query_params, org_slug)