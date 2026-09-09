# from odoo import http


# class AmKalsalTeco(http.Controller):
#     @http.route('/am_kalsal_teco/am_kalsal_teco', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/am_kalsal_teco/am_kalsal_teco/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('am_kalsal_teco.listing', {
#             'root': '/am_kalsal_teco/am_kalsal_teco',
#             'objects': http.request.env['am_kalsal_teco.am_kalsal_teco'].search([]),
#         })

#     @http.route('/am_kalsal_teco/am_kalsal_teco/objects/<model("am_kalsal_teco.am_kalsal_teco"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('am_kalsal_teco.object', {
#             'object': obj
#         })

