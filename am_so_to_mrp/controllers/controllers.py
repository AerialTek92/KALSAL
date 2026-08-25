# from odoo import http


# class AmSoToMrp(http.Controller):
#     @http.route('/am_so_to_mrp/am_so_to_mrp', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/am_so_to_mrp/am_so_to_mrp/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('am_so_to_mrp.listing', {
#             'root': '/am_so_to_mrp/am_so_to_mrp',
#             'objects': http.request.env['am_so_to_mrp.am_so_to_mrp'].search([]),
#         })

#     @http.route('/am_so_to_mrp/am_so_to_mrp/objects/<model("am_so_to_mrp.am_so_to_mrp"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('am_so_to_mrp.object', {
#             'object': obj
#         })

