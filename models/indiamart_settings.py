# -*- coding: utf-8 -*-
import requests
import logging
from datetime import datetime, timedelta
from odoo import fields, models, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class IndiaMARTSettings(models.Model):
    _name = 'indiamart.settings'
    _description = 'IndiaMART API Settings'

    name = fields.Char(default='IndiaMART API Configuration', readonly=True, required=True)
    api_key = fields.Char(string="IndiaMART API Key", help="The Pull API Key generated from your IndiaMART seller panel.")

    def action_test_connection(self):
        self.ensure_one()
        if not self.api_key:
            raise UserError("Please enter an IndiaMART API Key before testing the connection.")
        api_url = "https://mapi.indiamart.com/wservce/crm/crmListing/v2/"
        params = {'glusr_crm_key': self.api_key}
        try:
            response = requests.get(api_url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get('STATUS') == 'FAILURE':
                error_msg = data.get('MESSAGE', 'Unknown error.')
                raise UserError(f"IndiaMART API Error:\n\n{error_msg}")
            success_message = data.get('MESSAGE', 'Successfully connected to the IndiaMART API.')
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': 'Connection Successful!', 'message': success_message, 'type': 'success', 'sticky': False}}
        except requests.exceptions.RequestException as e:
            raise UserError(f"A network error occurred: {e}")
        except ValueError:
            raise UserError("Received an invalid response from the IndiaMART server. The API might be temporarily down.")

    @api.model
    def _run_scheduled_fetch(self):
        _logger.info("Starting IndiaMART scheduled lead fetch...")
        log_vals = {'is_manual': False}
        try:
            settings = self.env['indiamart.settings'].search([], limit=1)
            if not settings or not settings.api_key:
                raise Exception("IndiaMART fetch job skipped: API Key is not set.")

            end_time = datetime.now()
            start_time = end_time - timedelta(minutes=10)
            start_str = start_time.strftime('%d-%m-%Y%H:%M:%S')
            end_str = end_time.strftime('%d-%m-%Y%H:%M:%S')
            api_url = "https://mapi.indiamart.com/wservce/crm/crmListing/v2/"
            params = {'glusr_crm_key': settings.api_key, 'start_time': start_str, 'end_time': end_str}

            response = requests.get(api_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get('STATUS') == 'FAILURE':
                raise Exception(f"IndiaMART API Error: {data.get('MESSAGE')}")

            leads_data = data.get('RESPONSE', [])
            log_vals['leads_fetched'] = len(leads_data)

            new_leads_count = 0
            if leads_data:
                Lead = self.env['crm.lead']
                for lead in leads_data:
                    unique_id = lead.get('UNIQUE_QUERY_ID')
                    if not unique_id or Lead.search_count([('indiamart_unique_id', '=', unique_id)]):
                        continue

                    query_type = lead.get('QUERY_TYPE')
                    probability_map = {
                        'P': 75, 'W': 50, 'WA': 40, 'B': 25, 'BIZ': 10
                    }
                    probability = probability_map.get(query_type, 10)
                    company_name = lead.get('SENDER_COMPANY') or lead.get('SENDER_NAME')
                    lead_name = f"{lead.get('SENDER_NAME', 'N/A')} - {lead.get('SUBJECT', 'Inquiry')}"
                    description = (
                        f"IndiaMART Lead\n--------------------\n"
                        f"Subject: {lead.get('SUBJECT', 'N/A')}\nMessage: {lead.get('QUERY_MESSAGE', 'N/A')}\n\n"
                        f"Product Name: {lead.get('PRODUCT_NAME', 'N/A')}\nSender Location: {lead.get('SENDER_CITY', '')}, {lead.get('SENDER_STATE', '')}\n"
                        f"Query Type: {lead.get('QUERY_TYPE', 'N/A')}\n"
                    )
                    
                    vals = {
                        'type': 'lead',
                        'name': lead_name,
                        'partner_name': company_name,
                        'contact_name': lead.get('SENDER_NAME'),
                        'email_from': lead.get('SENDER_EMAIL'),
                        'phone': lead.get('SENDER_MOBILE'),
                        'street': lead.get('SENDER_ADDRESS'),
                        'city': lead.get('SENDER_CITY'),
                        'state_id': self.env['res.country.state'].search([('name', '=', lead.get('SENDER_STATE'))], limit=1).id,
                        'country_id': self.env['res.country'].search([('code', '=', lead.get('SENDER_COUNTRY_ISO'))], limit=1).id,
                        'description': description,
                        'indiamart_unique_id': unique_id,
                        'indiamart_query_type': query_type,
                        'probability': probability,
                    }
                    Lead.create(vals)
                    new_leads_count += 1
            
            message = f"Successfully created {new_leads_count} new lead(s)."
            log_vals.update({'status': 'success', 'leads_created': new_leads_count, 'response_message': message})
            _logger.info(f"IndiaMART fetch successful: {message}")

        except Exception as e:
            log_vals.update({'status': 'failure', 'response_message': str(e)})
            _logger.error(f"Failed to fetch IndiaMART leads: {e}", exc_info=True)
        finally:
            self.env['indiamart.api.log'].create(log_vals)