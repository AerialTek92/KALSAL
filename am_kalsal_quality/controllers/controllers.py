# from odoo import http


# class AmKalsalQuality(http.Controller):
#     @http.route('/am_kalsal_quality/am_kalsal_quality', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/am_kalsal_quality/am_kalsal_quality/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('am_kalsal_quality.listing', {
#             'root': '/am_kalsal_quality/am_kalsal_quality',
#             'objects': http.request.env['am_kalsal_quality.am_kalsal_quality'].search([]),
#         })

#     @http.route('/am_kalsal_quality/am_kalsal_quality/objects/<model("am_kalsal_quality.am_kalsal_quality"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('am_kalsal_quality.object', {
#             'object': obj
#         })

