__author__      = "Brandon Beck, Devarshi Shah, Chad Nijim"
__version__     = "1.0.0"

from tetpyclient import RestClient
import tetpyclient
import json
import requests.packages.urllib3
import sys
import os
import time
import csv

requests.packages.urllib3.disable_warnings()

def CreateRestClient(settings):
    rc = RestClient(settings['url'],
                    credentials_file=settings['credential'], verify=False)
    return rc

def GetApplicationScopes(rc):
    resp = rc.get('/app_scopes')

    if resp.status_code != 200:
        print("Failed to retrieve app scopes")
        print(resp.status_code)
        print(resp.text)
    else:
        return resp.json()

def GetAppScopeId(scopes,name):
    try:
        return [scope["id"] for scope in scopes if scope["name"] == name][0]
    except:
        print("App Scope {name} not found".format(name=name))

def CreateInventoryFiltersFromApi(rc,scopes,network_list,params):
    inventoryDict = {}
    for row in network_list:
        if row['comment'] not in inventoryDict:
            inventoryDict[row['comment']] = {}
            inventoryDict[row['comment']]['app_scope_id'] = GetAppScopeId(scopes,params['parentScope'])
            inventoryDict[row['comment']]['name'] = row['comment']
            inventoryDict[row['comment']]['primary'] = params['restricted']
            inventoryDict[row['comment']]['query'] = {
                "type" : "or",
                "filters" : []
            }
        if inventoryDict[row['comment']]['app_scope_id'] != GetAppScopeId(scopes,params['parentScope']):
            print("Parent scope for {network} does not match previous definition".format(network=row['network']))
            continue
        inventoryDict[row['comment']]['query']['filters'].append({
            "type": "subnet",
            "field": "ip",
            "value": row['network']
        })
    return inventoryDict

def CreateInventoryFiltersFromCsv(rc,scopes,filename):
    inventoryDict = {}
    try:
        with open(filename) as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row['Comment'] not in inventoryDict:
                    inventoryDict[row['Comment']] = {}
                    inventoryDict[row['Comment']]['app_scope_id'] = GetAppScopeId(scopes,row['ParentScope'])
                    inventoryDict[row['Comment']]['name'] = row['Comment']
                    inventoryDict[row['Comment']]['primary'] = row['Restricted'].lower()
                    inventoryDict[row['Comment']]['query'] = {
                        "type" : "or",
                        "filters" : []
                	}
            	if inventoryDict[row['Comment']]['app_scope_id'] != GetAppScopeId(scopes,row['ParentScope']):
                	print("Parent scope for {network} does not match previous definition".format(network=row['Network']))
                	continue
            	inventoryDict[row['Comment']]['query']['filters'].append({
                	"type": "subnet",
                	"field": "ip",
                	"value": row['Network']
            	})
    except IOError:
		logger.error("File %s does not exist. Please follow README steps to generate the file"%filename)
    return inventoryDict

def PushInventoryFilters(rc,inventoryFilters):
    for inventoryFilter in inventoryFilters:
        req_payload = inventoryFilters[inventoryFilter]
        resp = rc.post('/filters/inventories', json_body=json.dumps(req_payload))
        if resp.status_code != 200:
            print("Error pushing InventorFilter")
            print(resp.status_code)
            print(resp.text)
        else:
            print("Inventory Filters successfully pushed for " + inventoryFilters[inventoryFilter]["name"])

def AnnotateHosts(rc,hosts,params):
    columns = [params["columns"][column] for column in params["columns"] if params["columns"][column]["enabled"] == True]    
    with open(params["csvParams"]["exportFilename"], "wb") as csv_file:
	if params["tetrationVersion"] >= 2.3 and params["scopeDependent"]:
            fieldnames = ['IP']
	else:
	    fieldnames = ['IP','VRF']
        for column in columns:
            if column["infobloxName"] != 'extattrs':
                fieldnames.extend([column["annotationName"]])
            else:
                if column["overload"] == True:
                    fieldnames.extend([column["annotationName"]])
                else:
                    for attr in column["attributeList"]:
                        fieldnames.extend([str(column["annotationName"]) + '-' + str(attr)])
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for host in hosts:
            hostDict = {}
            hostDict["IP"] = host["ip_address"]
            if params["vrf"]["type"] == 'static' and (params["tetrationVersion"] < 2.3 or not params["scopeDependent"]):
                hostDict["VRF"] = params["vrf"]["value"]
            else:
                if params["vrf"]["eaName"] not in host["extattrs"]:
                    print("EA: " + params["vrf"]["eaName"] + "not defined for host: " + host["ip_address"] + " skipping....")
                    continue
		if (params["tetrationVersion"] < 2.3 or not params["scopeDependent"]):
                    hostDict["VRF"] = host["extattrs"][params["vrf"]["eaName"]]["value"]
            for column in columns:
                if column["infobloxName"] == 'extattrs':
                    for attr in column["attributeList"]:
                        if column["overload"] == True:
                            if attr in host["extattrs"]:
                                hostDict[column["annotationName"]] = str(attr) + '=' + str(host["extattrs"][attr]["value"]) + ';' if column["annotationName"] not in hostDict.keys() else hostDict[column["annotationName"]] + str(attr) + '=' + str(host["extattrs"][attr]["value"]) + ';'
                            else:
                                hostDict[column["annotationName"]] = str(attr) + '=;' if column["annotationName"] not in hostDict.keys() else str(hostDict[column["annotationName"]]) + str(attr) + '=;'
                        else:
                            if attr in host["extattrs"]:
                                hostDict[column["annotationName"] + '-' + attr] = host["extattrs"][attr]["value"]
                            else:
                                hostDict[column["annotationName"] + '-' + attr] = ''
                else:
                    hostDict[column["annotationName"]] = ",".join(host[column["infobloxName"]]).split('.')[0] if type(host[column["infobloxName"]]) is list else host[column["infobloxName"]]
            writer.writerow(hostDict)
    api_endpoint = "/assets/cmdb/upload"
    if params["tetrationVersion"] >= 2.3:
	if params["scopeDependent"]:
            api_endpoint = "/assets/cmdb/upload/" + params["vrf"]["scope"]
	    req_payload = [tetpyclient.MultiPartOption(key='X-Tetration-Oper', val='add')]
    else:
	keys = ['IP', 'VRF']
	req_payload = [tetpyclient.MultiPartOption(key='X-Tetration-Key', val=keys), tetpyclient.MultiPartOption(key='X-Tetration-Oper', val='add')]
    resp = rc.upload(params["csvParams"]["exportFilename"], api_endpoint, req_payload)
    if resp.status_code != 200:
        print("Error posting annotations to Tetration cluster"%resp.status_code)
    else:
        print("Successfully posted annotations to Tetration cluster")
