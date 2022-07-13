#!/usr/bin/env python
#
# Dummy IN-AE which simply registers to the given IN-CSE.
#
# Pre-requisites:
#
#   # apt install python-is-python3 python3-pip python3-virtualenv
#   # pip3 install aiohttp==3.7.4.post0 pytz requests Sphinx sphinx_rtd_theme
#   # mkdir -m 775 /var/log/metersummary

import os, sys, signal, configparser

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from client.onem2m.OneM2MPrimitive import OneM2MPrimitive
from client.cse.CSE import CSE
from client.ae.AE import AE

from typing import Final

from time import sleep


################# Configure the following for your environment #################

# The AE App and credential IDs, as generated in PolicyNet via More -> System settings -> AE Registration Credentials.
APP_ID: Final = 'Nregister'
AE_ID: Final = 'XXXXXXXXXXXXXXXX'

# Address of the IN-CSE running in your cloud environment.
CSE_PROTOCOL: Final = 'http'
CSE_HOST: Final = 'dev9.usw1.aws.corp.grid-net.com'
CSE_PORT: Final = 21300

# Identification of this IN-AE.
RESOURCE_NAME: Final = APP_ID[1:]
APP_NAME: Final = 'com.grid-net.' + RESOURCE_NAME

# File to store the persistent registration ID in.
SETTINGS_FILE: Final = '/var/tmp/register.ini'

############################## End of site config ##############################


# Create an instance of the CSE to send requests to.
pn_cse = CSE(host=CSE_HOST, port=CSE_PORT, transport_protocol=CSE_PROTOCOL)

# Persistent settings via INI file.
settings = configparser.ConfigParser()


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

        # Ensure that status output is printed immediately.
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")

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

        # Create a dummy AE instance to register with the CSE. We don't really expect a listener at localhost:7000.
        req_ae = AE(
            {
                AE.M2M_ATTR_APP_ID         : APP_ID,
                AE.M2M_ATTR_APP_NAME       : APP_NAME,
                AE.M2M_ATTR_AE_ID          : AE_ID,
                AE.M2M_ATTR_POINT_OF_ACCESS: ['http://localhost:7000'],
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
        rn = res.pc["m2m:ae"]["rn"]
        saveConfig(res.pc["m2m:ae"]["ri"])
        ri_persistent = res.pc["m2m:ae"]["ri"]

        print('AE registration successful: {}'.format(rn))

        # Sleep until killed.
        while True:
            sleep(3600)

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
