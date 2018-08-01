__author__      = "Brandon Beck, Devarshi Shah, Chad Nijim"
__version__     = "1.0.0"

import os 
import logging
#logging.basicConfig(level=logging.DEBUG)

from infoblox_client import connector
import yaml
import json
import tetration
import csv
import argparse
import sys
import requests
from requests.auth import HTTPBasicAuth
import netaddr

# ====================================================================================
# Logging
# ------------------------------------------------------------------------------------
LOG_FILENAME = os.path.dirname(os.path.realpath("__file__")) + "/logs/infoblox-integration.log"
LOG_LEVEL = logging.INFO  # Could be e.g. "DEBUG" or "WARNING"

# Configure logging to log to a file, making a new file at midnight and keeping the last 3 day's data
# Give the logger a unique name (good practice)
logger = logging.getLogger(__name__)
# Set the log level to LOG_LEVEL
logger.setLevel(LOG_LEVEL)
# Make a handler that writes to a file, making a new file at midnight and keeping 3 backups
handler = logging.handlers.TimedRotatingFileHandler(LOG_FILENAME, when="midnight", backupCount=3)
# Format each log message like this
formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')
# Attach the formatter to the handler
handler.setFormatter(formatter)
# Attach the handler to the logger
logger.addHandler(handler)

# Make a class we can use to capture stdout and sterr in the log
class MyLogger(object):
        def __init__(self, logger, level):
                """Needs a logger and a logger level."""
                self.logger = logger
                self.level = level

        def write(self, message):
                # Only log if there is a message (not just a new line)
                if message.rstrip() != "":
                        self.logger.log(self.level, message.rstrip())

# Replace stdout with logging to file at INFO level
sys.stdout = MyLogger(logger, logging.INFO)
# Replace stderr with logging to file at ERROR level
sys.stderr = MyLogger(logger, logging.ERROR)

# ====================================================================================
# GLOBALS
# ------------------------------------------------------------------------------------
# Read in settings
settings = yaml.load(open('settings.yml'))
# Connect to infoblox
conn = connector.Connector(settings['infoblox'])
# Connect to tetration   
rc = tetration.CreateRestClient(settings['tetration'])

# Debug function used for printing formatted dictionaries
def PrettyPrint(target):
    print json.dumps(target,sort_keys=True,indent=4)

def create_filter_csv(filename):
    # Get defined networks
    logger.info("Getting networks from infoblox")
    networks = conn.get_object('network')
    # Find networks with a comment defined
    network_list = [network for network in networks if 'comment' in network]
    logger.info("Writing filter csv to file:" + filename)
    with open(filename, "wb") as csv_file:
        writer = csv.writer(csv_file, delimiter=',')
        writer.writerow('Network,Comment,ParentScope,Restricted'.split(','))
        for line in network_list:
            writer.writerow([line["network"],line["comment"],'Default','TRUE'])
    logger.info("Create filter csv complete")

def create_network_csv(filename):
    logger.info("Getting networks from infoblox")
    networks = conn.get_object('network')
    logger.info("Writing network csv to file:" + filename)
    with open(filename, "wb") as csv_file:
        writer = csv.writer(csv_file, delimiter=',')
        writer.writerow('Network View,Network,Comment'.split(','))
        for line in networks:
            writer.writerow([line["network_view"],line["network"],line["comment"] if "comment" in line else ''])
    logger.info("Create network csv complete")

def import_extensible_attributes(filename,eaName,eaValue):
    networks = []
    logger.debug("Inside import extensible attributes")
    logger.debug("Opening CSV file named:" + filename)
    try:
        with open(filename, "rb") as csvFile:
            reader = csv.DictReader(csvFile)
            for row in reader:
                networks.append(row)
    except IOError:
	logger.error("File %s does not exist. Please follow README steps to generate the file"%filename)
	return
    url = 'https://' + settings["infoblox"]["host"] + '/wapi/v' + settings["infoblox"]["wapi_version"] + '/'
    s = requests.Session()
    s.auth = HTTPBasicAuth(settings["infoblox"]["username"],settings["infoblox"]["password"])
    s.verify = False
    s.headers['Content-Type'] = 'application/json'
    
    for network in networks:
        logger.info("Retrieving network:" + network["Network"] + " from infoblox")
        netObj = conn.get_object('network',{'network': network["Network"],'network_view': network["Network View"]})
        req_payload = {
            "extattrs": {
                eaName: {
                    "descendants_action":{
                        "option_with_ea": "RETAIN",
                        "option_without_ea": "INHERIT"
                    },
                    "value": eaValue
                }
            }
        }
        logger.info("Adding EA: " + eaName + " with value:" + eaValue + " to network:" + network["Network"])
        resp = s.put(url + netObj[0]["_ref"],data=json.dumps(req_payload))
        if resp.status_code != 200:
            logger.error("Error while applying extensible attribute: " + eaName)
        else:
            logger.info("Extensible attribute: " + eaName + " added to addresses in" + network["Network"])

def push_network_filters(filename):
    # Get Scopes
    logger.info("Creating network filters")
    logger.debug("Getting application scopes from tetration")
    scopes = tetration.GetApplicationScopes(rc)

    logger.info("Creating inventory filters from csv")
    inventoryFilters = tetration.CreateInventoryFiltersFromCsv(rc,scopes,filename)
    # Push Filters to Tetration
    logger.info("Pushing filters to tetration")
    tetration.PushInventoryFilters(rc,inventoryFilters)
    logger.info("Filters successfully pushed to tetration")

def create_network_filters(params):
    # Get Scopes
    logger.info("Creating network filters")
    logger.debug("Getting application scopes from tetration")
    scopes = tetration.GetApplicationScopes(rc)
    # Get defined networks
    networks = []
    if params["type"].lower() == 'all':
        logger.info("Getting all networks from infoblox")
        networks = conn.get_object('network',{'network_view': params["view"]} if params["view"] != '' else None)
        if networks is None:
            logger.warning("No networks were found in network view: " + params["view"])
            return
        # Find networks with a comment defined
        network_list = [network for network in networks if 'comment' in network]
        # Create API Query for creating inventory filters
        logger.info("Creating tetration inventory filters from API")
        inventoryFilters = tetration.CreateInventoryFiltersFromApi(rc,scopes,network_list,params['apiParams'])
    else:
	try:
            with open(params["csvParams"]["filename"], "rb") as csvFile:
                reader = csv.DictReader(csvFile)
                for row in reader:
                    logger.info("Getting network:" + row["Network"] + " from infoblox")
                    networks.extend(conn.get_object('network',{'network': row["Network"], 'network_view': row["Network View"]}))
            network_list = [network for network in networks if 'comment' in network]
            logger.info("Creating inventory filters from networks in csv: " + params["csvParams"]["filename"])
            inventoryFilters = tetration.CreateInventoryFiltersFromApi(rc,scopes,params['apiParams'])
	except IOError:
	    logger.error("File %s does not exist. Please follow README steps to generate the file"%params["csvParams"]["filename"])	
	    return
    # Push Filters to Tetration
    logger.info("Pushing filters to tetration")
    tetration.PushInventoryFilters(rc,inventoryFilters)
    logger.info("Filters successfully pushed to tetration")

def annotate_hosts(params):
    logger.info("Creating host annotations")
    hosts = []
    # Read hosts from networks listed in csv
    if params["type"] == 'csv':
        logger.info("Reading network list from csv:" + params["csvParams"]["importFilename"])
	try:
            with open(params["csvParams"]["importFilename"], "rb") as csvFile:
                reader = csv.DictReader(csvFile)
                for row in reader:
                    # Read all hosts with a name defined
		    host_obj = conn.get_object('ipv4address',{'network': row["Network"],'names~': '.*','_return_fields': 'network,network_view,names,ip_address,extattrs'})
		    if host_obj is not None:
                        hosts.extend(host_obj)
		  
	except IOError:
	    logger.error("File %s does not exist. Please follow README steps to generate the file"%params["csvParams"]["importFilename"])
    else:
        logger.info("Getting all networks from infoblox for view:" + params["view"])
        networks = conn.get_object('network',{'network_view': params["view"]} if params["view"] != '' else None)
        for network in [network["network"] for network in networks]:
            # Read all hosts with a name defined
            host_obj = conn.get_object('ipv4address',{'network': network,'names~': '.*', '_return_fields': 'network,network_view,names,ip_address,extattrs'} if params["view"] == '' else {'network': network, 'names~': '.*', '_return_fields': 'network,network_view,names,ip_address,extattrs','network_view': params["view"]})
            if host_obj is not None:
                hosts.extend(host_obj)
    logger.info("Creating annotations for selected networks")
    tetration.AnnotateHosts(rc,hosts,params)
    logger.info("Host annotation updates complete")

def main():
    parser = argparse.ArgumentParser(description='Tetration Infoblox Integration Script')
    parser.add_argument('--createFilterCsv', help='Filename for creating Filter Csv')
    parser.add_argument('--pushFilterCsv', help='Filename of csv containing filters to be manually pushed to tetration')
    parser.add_argument('--createNetworkCsv', help='Filename for creating extensible attributes csv')
    parser.add_argument('--importEaCsv', help='Filename for importing extensible attributes from csv')
    parser.add_argument('--importEaName', help='Extensible attribute name')
    parser.add_argument('--importEaValue', help='Extensible attribute value to be applied to network(s)')
    args = parser.parse_args()

    # Create CSV for defining inventory filters
    if args.createFilterCsv is not None:
        logger.info("Creating filter csv")
        create_filter_csv(args.createFilterCsv)

    if args.pushFilterCsv is not None:
        logger.info("Creating filter csv")
        push_network_filters(args.pushFilterCsv)

    if args.createNetworkCsv is not None:
        logger.info("Creating network csv")
        create_network_csv(args.createNetworkCsv)

    if args.importEaCsv is not None:
        if args.importEaName is None:
            print("Extensible attribute name required (--importEaName)")
            return
        logger.info("Importing extensible attribute for list of networks")
        import_extensible_attributes(args.importEaCsv,args.importEaName,args.importEaValue)

    if not len(sys.argv) > 1:
        # Iterate through actions from settings file
        for action,value in ((action,value) for action,value in (settings['actions']).iteritems() if value["enabled"] == True):
            globals()[action](value)

if __name__ == "__main__":
    main()
