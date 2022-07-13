#!/usr/bin/env python
#
# Standing data server.  Via oneM2M, takes a Meter ID or NMI and returns the requested device's mapping between elements and
# measurement points.
#
# Pre-requisites:
#
#   # apt install python-is-python3 python3-pip python3-virtualenv
#   # pip3 install aiohttp==3.7.4.post0 boto3 pytz requests Sphinx sphinx_rtd_theme
#   # mkdir -m 775 /var/log/standingdata

import os, sys, time, signal, threading, queue, socket, requests, json, boto3, pytz, configparser

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.onem2m.OneM2MPrimitive import OneM2MPrimitive
from client.onem2m.http.OneM2MRequest import OneM2MRequest
from client.onem2m.resource.Container import Container
from client.onem2m.resource.ContentInstance import ContentInstance
from client.cse.CSE import CSE
from client.ae.AE import AE
from client.ae.AsyncResponseListener import AsyncResponseListenerFactory
from client.Utility import Utility

from threading import Lock
from datetime import datetime
from typing import Final
from aiohttp import web
from boto3.dynamodb.conditions import Key, Attr


################# Configure the following for your environment #################

# AWS credentials.
AWS_ACCESS_KEY_ID: Final = 'ABCEDFGHIJKLMNOPQRSTUVWXYZ'
AWS_SECRET_ACCESS_KEY: Final = 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'

# The AE App and credential IDs, as generated in PolicyNet via More -> System settings -> AE Registration Credentials.
APP_ID: Final = 'Nstandingdata'
AE_ID: Final = 'XXXXXXXXXXXXXXXX'

# Address of the IN-CSE running in your cloud environment.
CSE_PROTOCOL: Final = 'http'
CSE_HOST: Final = 'dev9.usw1.aws.corp.grid-net.com'
CSE_PORT: Final = 21300

# Identification of this IN-AE.
RESOURCE_NAME: Final = APP_ID[1:]
APP_NAME: Final = 'com.grid-net.' + RESOURCE_NAME

# Timezone for log rotation.  A new log file is started at midnight in this timezone.
tz = pytz.timezone('Australia/Sydney')

# Dummy meter ID to use as a placeholder until there is a method to look it up from the IN-CSE.
METER_ID_DUMMY: Final = 'LG012345678'

# AWS region (Asia Pacific: Sydney) and table name parameter.
AWS_REGION: Final = 'ap-southeast-2'
AWS_PARAM_TABLE_NAME: Final = '/msi/standing_data/table_name'
AWS_CHECK_SECONDS: Final = 300

############################## End of site config ##############################


# MN-AE response container and content instance.
RESPONSE_CONTAINER: Final = 'standingData'

# Details of the (usually local) listener that the IN-CSE will send notifications to.
NOTIFICATION_PROTOCOL: Final = 'http'
NOTIFICATION_HOST: Final = Utility.myIpAddress()
NOTIFICATION_PORT: Final = 8083
NOTIFICATION_CONTAINER: Final = 'cnt-00001'
NOTIFICATION_SUBSCRIPTION: Final = 'sub-00001'
# TODO What resets the timer on this, i.e. when are old containers or their contents actually removed?
NOTIFICATION_CONTAINER_MAX_AGE: Final = 900
NOTIFICATION_LOG_DIR: Final = '/var/log/standingdata'
NOTIFICATION_LOG_PREFIX: Final = 'notification_log_'
NOTIFICATION_LOG_SUFFIX: Final = '.json'

MAP_CONTAINER: Final = 'map'
MAP_CONTAINER_PATH: Final = '{}/{}'.format(RESPONSE_CONTAINER, MAP_CONTAINER)
MAP_CONTAINER_MAX_AGE: Final = 48 * 3600

SETTINGS_FILE: Final = '/var/tmp/standingdata.ini'


# Create an instance of the CSE to send requests to.
pn_cse = CSE(CSE_HOST, CSE_PORT)

# Persistent settings via INI file.
settings = configparser.ConfigParser()

# Queue used to control the response writer thread.
responseQueue = queue.Queue()

# Mutex to enforce atomicity on log file writes.
logMutex = Lock()


# Query AWS standing data DynamoDB by either NMI or Meter ID.
# Returns an array of Items, each of which contains a unique mapping of elements to measurement points; or None on error.
def getStandingData(table=None, nmi=None, meterId=None):
    # If no table has been specified, open the last known good standing data table.
    if table is None:
        client = boto3.client('ssm', region_name=AWS_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
        table_name = client.get_parameter(Name=AWS_PARAM_TABLE_NAME)['Parameter']['Value']
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
        table = dynamodb.Table(table_name)
        print('Using standing data table {} ({}, {} items)'.format(table_name, table.status, table.item_count))

    if nmi is not None:
        # Query using the public key (NMI) directly.
        response = table.query(
            KeyConditionExpression=Key('PK').eq('NMI#{}'.format(nmi))
        )
    elif meterId is not None:
        # Query by index (currently only on meter ID).
        response = table.query(
            IndexName='GSI1',
            KeyConditionExpression=Key('GSI1PK').eq('METER#{}'.format(meterId))
        )
    else:
        return None

    if response['Count'] == 0:
        return None

    return response['Items']


# Take a JSON object containing an Items array, and return a JSON object containing minimal formed from the following fields:
#   * 'REGISTER_ID' -> 'reg'
#   * 'MEASUREMENT_POINT' -> 'mp'
#   * 'DIRECTION' -> 'dir'
def minimiseItems(items):
    mapArray = []
    for item in items:
        mapArray.append({
            'reg': item['REGISTER_ID'],
            'mp': item['MEASUREMENT_POINT'],
            'dir': item['DIRECTION']
        })
    return mapArray


# Take a JSON object, and return a string form of the JSON object with all double quotes replaced with single quotes.
def replaceDoubleQuotes(jsonObject):
    return json.dumps(jsonObject).replace('"', "'")


# Query the IN-CSE for a list of all active SmartHUBs.
# TODO This is a preliminary method; Aetheros to advise if there is a better way.
def getSmartHubs():
    containers = pn_cse.discover_containers(with_ae=False, lvl=1)
#    containers.dump('Discovering SmartHUB containers')
    print('Retrieved {} containers\n'.format(len(containers.pc['m2m:uril'])))

    smartHubs = []
    for container in containers.pc['m2m:uril']:
        if container.startswith('/PN_CSE/nod-'):
            smartHubs.append(container)

    print('Found {} smartHUBs\n'.format(len(smartHubs)))

    return smartHubs


# Query the IN-CSE for a list of all the standing data mapping containers in our IN-AE container.
def getMappingCIs(rn):
    containers = pn_cse.discover_containers(MAP_CONTAINER, lvl=1)
    containers.dump('Discovering mapping containers')
    print('Retrieved {} containers\n'.format(len(containers.pc['m2m:uril'])))

    # TODO Use the path we registered at to determine the initial portion to remove.
    smartHubs = []
    for container in containers.pc['m2m:uril']:
        if container.startswith(rn + '/'):
            smartHubs.append(container)

    print('Found {} smartHUBs\n'.format(len(smartHubs)))

    return smartHubs


# Take an array of container URIs, and return an array of meter IDs.
def getMeterIds(containers):
    meterIds = []
    for container in containers:
        meterIds.append(container[len('/PN_CSE/nod-'):])
    return meterIds


def createContentInstance(rn, table, meterId):
    # Get the standing data for the SmartHUB.
    standingData = minimiseItems(getStandingData(table=table, meterId=meterId))
    if standingData is None:
        print('No standing data for {}'.format(meterId))
        return False

    # DEBUG Dump the standing data.
    print('Standing data for {}: {}'.format(meterId, standingData))

    # First, retrieve the existing mapping content instance and compare it to the standing data.
    # If the content instance does not exist, create it.
    # If it exists but is out of date, delete and recreate it with the correct data.
    # Otherwise, leave it in place.
    pathToMapContainer = rn + '/' + MAP_CONTAINER
    pathToMeterContainer = pathToMapContainer + '/' + meterId
    pathToMeterCI = pathToMeterContainer + '/' + meterId
    standingDataExisting = None
    try:
        standingDataExisting = pn_cse.retrieve_content_instance(pathToMeterCI, with_ae=False)
    except requests.exceptions.HTTPError as e:
        print('No existing content instance for {}'.format(pathToMeterCI))

    if standingDataExisting is not None:
        if 'm2m:cin' in standingDataExisting.pc and 'con' in standingDataExisting.pc['m2m:cin'] and standingData == standingDataExisting.pc['m2m:cin']['con']:
            print('Standing data for {} are unchanged'.format(meterId))
            return True

        print('New standing data for {} do not match old: {}, {}'.format(meterId, standingDataExisting.pc['m2m:cin']['con'], standingData))
        print('Deleting existing mapping content instance')
        pn_cse.delete_resource(pathToMeterCI)
    else:
        print('No prior standing data CI; creating container')
        content = Container({'rn': meterId, 'mia': MAP_CONTAINER_MAX_AGE})
        pn_cse.create_resource(pathToMapContainer, None, content, OneM2MRequest.M2M_RCN_HIERARCHICAL_ADDRESS)

    print('Creating new mapping content instance')
    content = ContentInstance({'rn': meterId, 'con': standingData})
    pn_cse.create_resource(pathToMeterContainer, None, content)

    print('CI for meter ID {} updated'.format(meterId))

    return True


def saveConfig(ri):
    settings.set('DEFAULT', 'ri_persistent', ri)
    with open(SETTINGS_FILE, 'w') as inifile:
        settings.write(inifile)


# Term signal handler to perform deregistration at shutdown.
def handleSignalTerm(signal, frame):
    if pn_cse.ae is not None:
        del_res = pn_cse.delete_ae()
        del_res.dump('Delete AE')

    saveConfig('')

    sys.exit(0)


def main():
    try:
        signal.signal(signal.SIGTERM, handleSignalTerm)

        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")

        # Confirm that there isn't already an instance running, using the HTTP listening port as a lock.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                bindres = sock.bind(('', NOTIFICATION_PORT))
                if bindres is not None and bindres != 0:
                    print('Error binding to port {}: {}'.format(NOTIFICATION_PORT, os.strerror(bindres)))
                    sys.exit(-1)
            except socket.error as msg:
                print('Error binding to port {}: {}'.format(NOTIFICATION_PORT, msg))
                sys.exit(-1)
            sock.close()

        # Open persistent setting file, or create if it doesn't exist.
        if settings.read(SETTINGS_FILE) == []:
            with open(SETTINGS_FILE, 'w') as fp:
                print('[DEFAULT]\nri_persistent = ', file=fp)
                fp.close()
                settings.read(SETTINGS_FILE)

        # If we did not cleanly exit last time, clean up the previous registration before continuing.
        ri_persistent = settings.get('DEFAULT', 'ri_persistent')
        if ri_persistent is not None and ri_persistent != '' and ri_persistent != "":
            print('Deregistering AE "{}" with CSE @ {}'.format(ri_persistent, CSE_HOST))
            to_ae = '{}://{}:{}/PN_CSE/{}'.format(pn_cse.transport_protocol, pn_cse.host, pn_cse.port, ri_persistent)
            res = pn_cse.delete_ae(to_ae, ri_persistent)
            res.dump('Deregister AE')
            saveConfig('')

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

        print('Registering AE "{}" with CSE @ {}'.format(req_ae.aei, CSE_HOST))

        # Register with the specified resourceName (or, if it is None, let the IN-CSE allocate one).
        res = pn_cse.register_ae(req_ae, RESOURCE_NAME)
        res.dump('Register AE')

        if res.rsc != OneM2MPrimitive.M2M_RSC_CREATED:
            print('Could not register AE\nExiting...')
            sys.exit(-2)

        # Save the name and RI we registered as.
        rn = res.pc['m2m:ae']['rn']
        saveConfig(res.pc['m2m:ae']['ri'])

        print('AE registration successful: {}'.format(rn))

        # Create a container to contain the mapping content instances.
        print('Creating container {}/{}'.format(rn, MAP_CONTAINER))
        content = Container({'rn': MAP_CONTAINER, 'mia': MAP_CONTAINER_MAX_AGE})
        res = pn_cse.create_resource('/PN_CSE/' + RESOURCE_NAME, None, content, OneM2MRequest.M2M_RCN_HIERARCHICAL_ADDRESS, with_rsc=False)
        res.dump('Create Container')

        lkgTableName = ''
        while True:
            # Once every AWS_CHECK_SECONDS seconds, read the /msi/standing_data/table_name parameter to check if there is a new last
            # known good standing data table.  We don't handle the case of the parameter changing again before we finish updating the
            # standing data content instances, but realistically it shouldn't take us hours to perform the update.
            client = boto3.client('ssm', region_name=AWS_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID,
                                                                 aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
            table_name = client.get_parameter(Name=AWS_PARAM_TABLE_NAME)['Parameter']['Value']
            if table_name != lkgTableName:
                if lkgTableName == '':
                    table_primacy = 'Initial'
                else:
                    table_primacy = 'New'
                print('{} standing data table {}'.format(table_primacy, table_name))
                lkgTableName = table_name
                dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID,
                                                                              aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
                table = dynamodb.Table(table_name)

                # Get a list of the meter IDs of all active SmartHUBs registered with the IN-CSE.
                shubNodes = getMeterIds(getSmartHubs())

                # Process each registered SmartHUB, aiming to create a standing data content instance for each.
#                for shubNode in shubNodes:
#                    createContentInstance(rn, table, shubNode)
                # DEBUG With no current way to look up meter IDs from IMEIs, hardcode one instead:
                createContentInstance(rn, table, METER_ID_DUMMY)
                time.sleep(5)
                createContentInstance(rn, table, METER_ID_DUMMY)

                # Get the list of all the mapping content instances: both those just created/updated, and pre-existing ones.
                shubCIs = getMeterIds(getMappingCIs(rn))

                # If a mapping content instance exists, but the corresponding SmartHUB is not registered, delete the content instance.
                for shubCI in shubCIs:
                    # TODO Is reg correct here?
                    if shubCI['reg'] not in shubNodes:
                        print('Deleting mapping content instance for {}'.format(shubCI['reg']))
                        res = pn_cse.delete_resource(rn, shubCI['reg'])
                        res.dump('Delete CI')

            time.sleep(AWS_CHECK_SECONDS)
            continue

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

        saveConfig('')


if __name__ == '__main__':
    main()
