#!/usr/bin/env python

# Example meter service receiver, designed to work with the December 2021 version of the aos-metering-app to demonstrate
# bidirectional, end-to-end communications.

import os, sys, threading, queue, requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.onem2m.OneM2MPrimitive import OneM2MPrimitive
from client.onem2m.http.OneM2MRequest import OneM2MRequest
from client.onem2m.resource.Container import Container
from client.onem2m.resource.ContentInstance import ContentInstance
from client.cse.CSE import CSE
from client.ae.AE import AE
from client.ae.AsyncResponseListener import AsyncResponseListenerFactory
from client.Utility import Utility

from typing import Final
from aiohttp import web


################# Configure the following for your environment #################

# The AE App and credential IDs, as generated in PolicyNet via More -> System settings -> AE Registration Credentials.
APP_ID: Final = 'Nmeterdemo'
AE_ID: Final = 'XXXXXXXXXXXXXXXX'

# Address of the IN-CSE running in your cloud environment.
CSE_PROTOCOL: Final = 'http'
CSE_HOST: Final = 'dev9.usw1.aws.corp.grid-net.com'
CSE_PORT: Final = 21300

# Identification of this IN-AE.
RESOURCE_NAME: Final = APP_ID[1:]
APP_NAME: Final = 'com.grid-net.' + RESOURCE_NAME

############################## End of site config ##############################


# MN-AE configuration container and content instance: in this example, the report interval, in seconds.
SEND_CONFIG: Final = True
CONFIG_CONTAINER: Final = 'meterSummary'
CONFIG_RESOURCE_NAME: Final = 'reportInterval'
CONFIG_CONTENT: Final = 120

# Details of the (usually local) listener that the IN-CSE will send notifications to.
NOTIFICATION_PROTOCOL: Final = 'http'
NOTIFICATION_HOST: Final = Utility.myIpAddress()
NOTIFICATION_PORT: Final = 8080
NOTIFICATION_CONTAINER: Final = 'cnt-00001'
NOTIFICATION_SUBSCRIPTION: Final = 'sub-00001'
NOTIFICATION_CONTAINER_MAX_AGE: Final = 900
NOTIFICATION_LOG: Final = 'notification_log.txt'


# Create an instance of the CSE to send requests to.
pn_cse = CSE(CSE_HOST, CSE_PORT)

# Queue used to control the configWorker thread.
configQueue = queue.Queue()

# Thread to asynchronously send configuration commands to MN-AEs that report in.
def configWorker():
    while True:
        config_path = configQueue.get()
        print('Creating configuration content instance {}'.format(config_path))
        content = ContentInstance({'rn': CONFIG_RESOURCE_NAME, 'con': CONFIG_CONTENT})

        assert pn_cse.ae is not None
        to = '{}://{}:{}{}'.format(CSE_PROTOCOL, CSE_HOST, CSE_PORT, config_path)
        params = {
            OneM2MPrimitive.M2M_PARAM_FROM: pn_cse.ae.ri,
            OneM2MPrimitive.M2M_PARAM_RESULT_CONTENT: 2,
            OneM2MPrimitive.M2M_PARAM_RESOURCE_TYPE: OneM2MPrimitive.M2M_RESOURCE_TYPES.ContentInstance.value,
        }

        content_instance = content
        oneM2MRequest = OneM2MRequest()

        try:
            response = oneM2MRequest.create(to, params, content_instance)
            response.dump('Configuration Content Instance')
        except requests.exceptions.HTTPError as e:
            print("Error: Configuration content instance creation failed with error {}".format(e.response.status_code))

            configQueue.task_done()

def main():
    try:
        # Open the log file in append mode.
        logFile = open(NOTIFICATION_LOG, 'a')

        # Create an AE instance to register with the CSE.
        NOTIFICATION_URI: Final = '{}://{}:{}'.format(NOTIFICATION_PROTOCOL, NOTIFICATION_HOST, NOTIFICATION_PORT)
        req_ae = AE(
            {
                AE.M2M_ATTR_APP_ID         : APP_ID,
                AE.M2M_ATTR_APP_NAME       : APP_NAME,
                AE.M2M_ATTR_AE_ID          : AE_ID,
                AE.M2M_ATTR_POINT_OF_ACCESS: [NOTIFICATION_URI],
            }
        )

        # Start the configuration worker thread.
        threading.Thread(target=configWorker, daemon=True).start()

        print('Registering AE "{}" with CSE @ {}'.format(req_ae.aei, CSE_HOST))

        # Register with the specified resourceName (or, if it is None, let the IN-CSE allocate one).
        res = pn_cse.register_ae(req_ae, RESOURCE_NAME)
        res.dump('Register AE')

        if res.rsc != OneM2MPrimitive.M2M_RSC_CREATED:
            print('Could not register AE\nExiting...')
            sys.exit()

        # Save the name we registered as.
        rn = res.pc["m2m:ae"]["rn"]

        print('AE registration successful: {}'.format(rn))

        # Example: Discover registered nodes.
#        print('Discovering nodes:')
#        containers = pn_cse.discover_nodes()
#        containers.dump('Discover Nodes')
#        print('Retrieved {} nodes\n'.format(len(containers.pc["m2m:uril"])))

        # Create a new container.
        print('Creating container {}/{}'.format(rn, NOTIFICATION_CONTAINER))
        content = Container({'rn': NOTIFICATION_CONTAINER, 'mia': NOTIFICATION_CONTAINER_MAX_AGE})
        res = pn_cse.create_resource(rn, None, content, OneM2MRequest.M2M_RCN_HIERARCHICAL_ADDRESS)
        res.dump('Create Container')

        # Create a subscription to the container.
        print('Subscribing to container: {}/{}'.format(rn, NOTIFICATION_CONTAINER))
        sub_res = pn_cse.create_subscription(rn + '/' + NOTIFICATION_CONTAINER, NOTIFICATION_SUBSCRIPTION, NOTIFICATION_URI, [3],
                                             OneM2MRequest.M2M_RCN_HIERARCHICAL_ADDRESS)
        sub_res.dump('Create Subscription')

        # Get the request ID to register with the async response handler.
        # NOTE The key we actually need isn't the RI, but rather the subscription URI.
        request_id = sub_res.pc["m2m:uri"]

        # Example: Retrieve the latest content instance.
#        print('Listing content instances in container: {}'.format(rn))
#        instance = pn_cse.retrieve_content_instance(rn)
#        instance.dump('Instance')

        # Example: Create a content instance.
#        print('Creating content instance of resource {}'.format("foobar"))
#        content = ContentInstance({'con': 'default content'})
#        res = pn_cse.create_content_instance("foobar", content)
#        res.dump('Create Content Instance')

        # Callback that will be execute whenever an HTTP request is sent to localhost:8080
        # and X-M2M-RI header is set.  The handler functions should process the request and
        # return the appropriate HTTP response orginator.
        # @todo AsyncResponseListener needs further refinement.  It should work with OneM2M primitives, not
        # HTTP messages directly.
        # Params are aiohttp request and response instance.
        # https://docs.aiohttp.org/en/stable/web_reference.html?highlight=Request#request-and-base-request
        # https://docs.aiohttp.org/en/stable/web_reference.html?highlight=Response#response-classes
        async def request_handler(req: web.Request, res: web.Response):
            #  Process request.
            if req.method == 'POST' or req.body_exists():
                # Modify response.
                res.headers.popall('Content-Type', "")
                res.headers['X-M2M-RSC'] = '2000'
                res.headers['X-M2M-RI'] = req.headers.get('X-M2M-RI')

                # Print and log the JSON.
                body = await req.json()
                if body is not None:
                    print(body)

                # Use the incoming report as a trigger to configure the MN-AE.
                if SEND_CONFIG:
                    cr = body['m2m:sgn']['nev']['rep']['m2m:cin']['cr']
                    if (cr is not None):
                        path = '/~/{}/{}'.format(cr, CONFIG_CONTAINER)
                        configQueue.put(path)

            return res

        handlerFactory = (
            AsyncResponseListenerFactory(NOTIFICATION_HOST, NOTIFICATION_PORT)
        )
        handler = handlerFactory.get_instance()
        handler.set_rqi_cb(
            request_id, request_handler
        )  # Map request ID to corresponding handler function.
        handler.run()

    except Exception as err:
        print('Exception raised...\n')
        print(err)
        if err.response is not None and err.response.text is not None:
            print(err.response.text)
    finally:
        print('Cleaning up...')
        # Clean up AE.
        if pn_cse.ae is not None:
            del_res = pn_cse.delete_ae()
            del_res.dump('Delete AE')


if __name__ == '__main__':
    main()
