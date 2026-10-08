# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID
from .hooks import post_init_hook

def migrate(cr, version):
    """
    Triggers automatically whenever the module is upgraded.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    post_init_hook(env)
