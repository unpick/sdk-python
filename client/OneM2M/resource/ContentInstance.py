from client.OneM2M.OneM2MResource import OneM2MResource

# {
#     "cin": {
#         "con": null,
#         "cr": "C64bb89740006ed",
#         "cs": 4,
#         "ct": "20200921T205114",
#         "et": "20200921T205114",
#         "lt": "20200921T205114",
#         "pi": "cnt642f2a00000272"p iii,
#         "ri": "cin64bb897a0006ee",
#         "rn": "cntPolicy3554",
#         "st": 0,
#         "ty": 4
#     }
# }
class ContentInstance(OneM2MResource):
    def __init__(self, cin):
        self.__dict__ = cin
        # Resource short name.
        OneM2MResource.short_name = 'cin'

