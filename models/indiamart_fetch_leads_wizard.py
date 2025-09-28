# -*- coding: utf-8 -*-
import requests
from datetime import datetime, timedelta
from odoo import fields, models, api
from odoo.exceptions import UserError, ValidationError

class IndiaMARTFetchLeadsWizard(models.TransientModel):
    _name = 'indiamart.fetch.leads.wizard'
    _description = 'IndiaMART Fetch Leads Wizard'

    start_time = fields.Datetime(string="Start Date", required=True, default=lambda self: datetime.now() - timedelta(days=1))
    end_time = fields.Datetime(string="End Date", required=True, default=lambda self: datetime.now())

    @api.constrains('start_time', 'end_time')
    def _check_dates(self):
        for record in self:
            if record.start_time >= record.end_time:
                raise ValidationError("Error: Start Date must be before End Date.")
            if record.end_time - record.start_time > timedelta(days=7):
                raise ValidationError("Error: The date range cannot be more than 7 days.")

    def action_fetch_leads(self):
        log_vals = {'is_manual': True}
        try:
            settings = self.env['indiamart.settings'].search([], limit=1)
            if not settings or not settings.api_key:
                raise UserError("IndiaMART API Key is not set. Please configure it in IndiaMART -> Configuration.")

            start_str = self.start_time.strftime('%d-%m-%Y%H:%M:%S')
            end_str = self.end_time.strftime('%d-%m-%Y%H:%M:%S')

            api_url = "https://mapi.indiamart.com/wservce/crm/crmListing/v2/"
            params = {'glusr_crm_key': settings.api_key, 'start_time': start_str, 'end_time': end_str}

            response = requests.get(api_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data.get('STATUS') == 'FAILURE':
                raise UserError(f"IndiaMART API Error: {data.get('MESSAGE')}")

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

            log_vals.update({'status': 'success', 'leads_created': new_leads_count, 'response_message': f"Successfully created {new_leads_count} new lead(s)."})
            return self._show_notification("Success!", log_vals['response_message'])

        except Exception as e:
            log_vals.update({'status': 'failure', 'response_message': str(e)})
            raise UserError(str(e))
        finally:
            self.env['indiamart.api.log'].create(log_vals)

    def _show_notification(self, title, message):
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': title, 'message': message, 'type': 'success', 'sticky': False}}