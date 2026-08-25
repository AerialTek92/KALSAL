# from odoo import http


# class KalsalPrSlip(http.Controller):
#     @http.route('/kalsal_pr_slip/kalsal_pr_slip', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/kalsal_pr_slip/kalsal_pr_slip/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('kalsal_pr_slip.listing', {
#             'root': '/kalsal_pr_slip/kalsal_pr_slip',
#             'objects': http.request.env['kalsal_pr_slip.kalsal_pr_slip'].search([]),
#         })

#     @http.route('/kalsal_pr_slip/kalsal_pr_slip/objects/<model("kalsal_pr_slip.kalsal_pr_slip"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('kalsal_pr_slip.object', {
#             'object': obj
#         })

