# -*- encoding: utf-8 -*-
"""
    Customizes party address to have address in correct format for UPS API .

"""
import re

# Remove when we are on python 3.x :)
from logbook import Logger

from ups.worldship_api import WorldShip
from ups.shipping_package import ShipmentConfirm
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction

import fulfil_shipping
from fulfil_shipping.validators import UPSClient
from fulfil_shipping.exceptions import ShippingException


__all__ = ['Address']
__metaclass__ = PoolMeta

digits_only_re = re.compile('\D+')
logger = Logger('trytond_ups')


class Address:
    '''
    Address
    '''
    __name__ = "party.address"

    @classmethod
    def __setup__(cls):
        super(Address, cls).__setup__()
        cls._error_messages.update({
            'ups_field_missing':
                '%s is missing in %s.'
        })

    @property
    def fulfil_shipping_obj(self):
        return fulfil_shipping.Address(
            name=self.name,
            street1=self.street,
            city=self.city,
            zip=self.zip,
            state=self.subdivision and self.subdivision.code.split('-')[1],
            country=self.country and self.country.code,
        )

    def _get_ups_address_xml(self):
        """
        Return Address XML
        """
        if not all([self.street, self.city, self.country]):
            self.raise_user_error("Street, City and Country are required.")

        if self.country.code in ['US', 'CA'] and not self.subdivision:
            self.raise_user_error(
                "State is required for %s" % self.country.code
            )

        if self.country.code in ['US', 'CA', 'PR'] and not self.zip:
            # If Shipper country is US or Puerto Rico, 5 or 9 digits is
            # required. The character - may be used to separate the first five
            # digits and the last four digits. If the Shipper country is CA,
            # then the postal code is required and must be 6 alphanumeric
            # characters whose format is A#A#A# where A is an uppercase letter
            # and # is a digit. For all other countries the postal code is
            # optional and must be no more than 9 alphanumeric characters long.
            self.raise_user_error("ZIP is required for %s" % self.country.code)

        vals = {
            'AddressLine1': self.street[:35],  # Limit to 35 Char
            'City': self.city[:30],  # Limit 30 Char
            'CountryCode': self.country.code,
        }

        if self.streetbis:
            vals['AddressLine2'] = self.streetbis[:35]  # Limit to 35 char
        if self.subdivision:
            # TODO: Handle Ireland Case
            vals['StateProvinceCode'] = self.subdivision.code[3:]
        if self.zip:
            vals['PostalCode'] = self.zip

        return ShipmentConfirm.address_type(**vals)

    def to_ups_from_address(self):
        '''
        Converts party address to UPS `From Address`.

        :return: Returns instance of FromAddress
        '''
        Company = Pool().get('company.company')

        vals = {}
        if not self.party.phone:
            self.raise_user_error(
                "ups_field_missing",
                error_args=('Phone no.', '"from address"')
            )

        company_id = Transaction().context.get('company')
        if not company_id:
            self.raise_user_error(
                "ups_field_missing",
                error_args=('Company', 'context')
            )

        company_party = Company(company_id).party

        vals = {
            'CompanyName': company_party.name,
            'AttentionName': self.name or self.party.name,
            'TaxIdentificationNumber': company_party.identifiers and
            company_party.identifiers[0].code or '',
            'PhoneNumber': digits_only_re.sub('', self.party.phone),
        }

        fax = self.party.fax
        if fax:
            vals['FaxNumber'] = fax

        # EMailAddress
        email = self.party.email
        if email:
            vals['EMailAddress'] = email

        return ShipmentConfirm.ship_from_type(
            self._get_ups_address_xml(), **vals)

    def to_ups_to_address(self):
        '''
        Converts party address to UPS `To Address`.

        :return: Returns instance of ToAddress
        '''
        party = self.party

        tax_identification_number = ''
        if party.identifiers:
            tax_identification_number = party.identifiers[0].code
        elif hasattr(party, 'tax_exemption_number') and \
                party.tax_exemption_number:
            tax_identification_number = party.tax_exemption_number

        vals = {
            'CompanyName': self.name or party.name,
            'TaxIdentificationNumber': tax_identification_number,
            'AttentionName': self.name or party.name,
        }

        if party.phone:
            vals['PhoneNumber'] = digits_only_re.sub('', party.phone)

        fax = party.fax
        if fax:
            vals['FaxNumber'] = fax

        # EMailAddress
        email = party.email
        if email:
            vals['EMailAddress'] = email

        # TODO: LocationID is optional

        return ShipmentConfirm.ship_to_type(self._get_ups_address_xml(), **vals)

    def to_ups_shipper(self, carrier):
        '''
        Converts party address to UPS `Shipper Address`.

        :return: Returns instance of ShipperAddress
        '''
        Company = Pool().get('company.company')

        vals = {}
        if not self.party.phone:
            self.raise_user_error(
                "ups_field_missing",
                error_args=('Phone no.', '"Shipper Address"')
            )

        company_id = Transaction().context.get('company')
        if not company_id:
            self.raise_user_error(
                "ups_field_missing", error_args=('Company', 'context')
            )

        company_party = Company(company_id).party

        vals = {
            'CompanyName': company_party.name,
            'TaxIdentificationNumber': company_party.identifiers and
            company_party.identifiers[0].code or '',
            'Name': self.name or self.party.name,
            'AttentionName': self.name or self.party.name,
            'PhoneNumber': digits_only_re.sub('', self.party.phone),
            'ShipperNumber': carrier.ups_shipper_no,
        }

        fax = self.party.fax
        if fax:
            vals['FaxNumber'] = fax

        # EMailAddress
        email = self.party.email
        if email:
            vals['EMailAddress'] = email

        return ShipmentConfirm.shipper_type(
            self._get_ups_address_xml(),
            **vals
        )

    def _ups_address_validate(self):
        """
        Validates the address using the PyUPS API.

        .. tip::

            This method is not intended to be called directly. It is
            automatically called by the address validation API of
            trytond-shipping module.
        """
        Subdivision = Pool().get('country.subdivision')
        Address = Pool().get('party.address')
        PartyConfig = Pool().get('party.configuration')

        config = PartyConfig(1)
        carrier = config.default_validation_carrier

        if not carrier:
            # TODO: Make this translatable error message
            self.raise_user_error(
                "Validation Carrier is not selected in party configuration."
            )

        client = UPSClient(
            license_number=carrier.ups_license_key,
            user_id=carrier.ups_user_id,
            password=carrier.ups_password,
            test_mode=carrier.ups_is_test,
        )

        try:
            response, = client.validate(self.fulfil_shipping_obj)
        except ShippingException as exc:
            self.raise_user_error(
                  "Error while validating address: %s" % exc.message[1]
              )

        if response['valid'] and not response['suggestions']:
            # Perfect match
            return True

        # This part is sadly static... wish we could verify more than the
        # state and city... like the street.
        base_address = {
            'name': self.name,
            'street': self.street,
            'streetbis': self.streetbis,
            'country': self.country,
            'zip': self.zip,
        }
        matches = []
        for address in response['suggestions']:
            try:
                subdivision, = Subdivision.search([
                    ('code', '=', '%s-%s' % (
                        self.country.code, address['state']
                    ))
                ])
            except ValueError:
                # If a unique match cannot be found for the subdivision,
                # we wont be able to save the address anyway.
                continue

            if (self.city.upper() == address['city'].upper()) and \
                    (self.subdivision == subdivision):
                # UPS does not know it, but this is a right address too
                # because we are suggesting exactly what is already in the
                # address.
                return True

            matches.append(
                Address(
                    city=address['city'], subdivision=subdivision, **base_address)
            )

        return matches

    def to_worldship_address(self):
        """
        Return the dict for worldship address xml
        """
        Company = Pool().get('company.company')

        vals = {}

        company_id = Transaction().context.get('company')
        if not company_id:
            self.raise_user_error(
                "ups_field_missing",
                error_args=('Company', 'context')
            )
        company_party = Company(company_id).party

        vals = {
            'CompanyOrName': company_party.name,
            'Attention': self.name or self.party.name,
            'Address1': self.street or '',
            'Address2': self.streetbis or '',
            'CountryTerritory': self.country and self.country.code,
            'PostalCode': self.zip or '',
            'CityOrTown': self.city or '',
            'StateProvinceCounty':
                self.subdivision and self.subdivision.code[3:],
            'Telephone': digits_only_re.sub('', self.party.phone),
        }
        return vals

    def to_worldship_to_address(self):
        """
        Return xml object of to address
        """
        values = self.to_worldship_address()
        values['CompanyOrName'] = self.name or self.party.name
        return WorldShip.ship_to_type(**values)

    def to_worldship_from_address(self):
        """
        Return xml object from address
        """
        values = self.to_worldship_address()
        return WorldShip.ship_from_type(**values)
