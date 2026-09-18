# from odoo import http


# class AmBomVersionsFinalized(http.Controller):
#     @http.route('/am_bom_versions_finalized/am_bom_versions_finalized', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/am_bom_versions_finalized/am_bom_versions_finalized/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('am_bom_versions_finalized.listing', {
#             'root': '/am_bom_versions_finalized/am_bom_versions_finalized',
#             'objects': http.request.env['am_bom_versions_finalized.am_bom_versions_finalized'].search([]),
#         })

#     @http.route('/am_bom_versions_finalized/am_bom_versions_finalized/objects/<model("am_bom_versions_finalized.am_bom_versions_finalized"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('am_bom_versions_finalized.object', {
#             'object': obj
#         })

