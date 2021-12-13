from client.onem2m.OneM2MResource import OneM2MResource, OneM2MResourceContent
from client.onem2m.OneM2MPrimitive import OneM2MPrimitive

# {
#     "m2m:cnt": {
#         "cbs": 0,
#         "cni": 0,
#         "cr": "C1636064176x000015",
#         "ct": "20211105T014934",
#         "et": "20211105T014934",
#         "lt": "20211105T014934",
#         "mia": 86400,
#         "pi": "C1636064176x000015",
#         "ri": "cnt1636064176x000016",
#         "rn": "cnt-00001",
#         "st": 0,
#         "ty": 3
#     }
# }
class Container(OneM2MResource):

    CONTENT_TYPE = OneM2MPrimitive.M2M_RESOURCE_TYPES.Container.value

    def __init__(self, cnt: OneM2MResourceContent):
        super().__init__('m2m:cnt', cnt)
