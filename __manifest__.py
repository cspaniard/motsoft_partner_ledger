# -*- coding: utf-8 -*-
{
    'name': "motsoft_partner_ledger",

    'summary': """
        Libro de Mayor por Empresa con formato mejorado.
        """,

    'description': """
        Libro de Mayor por Empresa con formato mejorado.
    """,

    'author': "My Company",
    'website': "http://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Invoicing Management',
    'version': '12.0.0.1',

    # any module necessary for this one to work correctly
    'depends': [
        'base',
        'accounting_pdf_reports'
    ],

    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'wizard/partner_ledger.xml',
        'reports/partner_ledger_report.xml',
        # 'views/templates.xml',
    ],
}
