from odoo import models


def _action_custom_save(self):
    """Handler for the global custom Save button.

    The web client writes all pending form changes to the database before
    invoking any object button, so this method only returns a non-action
    value to keep the user on the current form.
    """
    return True


# Register on the base ORM class so EVERY model (core and custom)
# can expose the button without per-model Python changes.
models.Model.action_custom_save = _action_custom_save