from client.onem2m.OneM2MResource import OneM2MResource, OneM2MResourceContent
from client.onem2m.OneM2MPrimitive import OneM2MPrimitive

# {
#     "sub": {
# 	"enc": {
# 		"net": [ ${NET} ],
# 		"ty": 4
# 	},
# 	"nct": ${NCT},
# 	"nu": ["${NU}"]
#   }
# }
class Subscription(OneM2MResource):
    # Resource specific criteria.
    # @todo add remaining resource attribute from TS-0004 8.2.3
    M2M_ATTR_EVENT_NOTIFICATION_CRITERIA = 'm2m:enc'
    M2M_ATTR_NOTIFICATION_URI            = 'nu'
    M2M_ATTR_NCT                         = 'nct'     # @note can not find in docs.

    CONTENT_TYPE = OneM2MPrimitive.M2M_RESOURCE_TYPES.Subscription.value

    def __init__(self, subscription: OneM2MResourceContent):
        """
        """
        super().__init__('m2m:sub', subscription)
