#      Copyright 2015, Schuberg Philis BV
#
#      Licensed to the Apache Software Foundation (ASF) under one
#      or more contributor license agreements.  See the NOTICE file
#      distributed with this work for additional information
#      regarding copyright ownership.  The ASF licenses this file
#      to you under the Apache License, Version 2.0 (the
#      "License"); you may not use this file except in compliance
#      with the License.  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#      Unless required by applicable law or agreed to in writing,
#      software distributed under the License is distributed on an
#      "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#      KIND, either express or implied.  See the License for the
#      specific language governing permissions and limitations
#      under the License.

# Class to support operations specific to Leaseweb
# Nuno Tavares - n.tavares@tech.leaseweb.com

# Import the class we depend on
from cloudstackops import CloudStackOps
from lswcloudstackopsbase import LswCloudStackOpsBase
import os.path
import pprint
import signal
import re
import sys

try:
    import dns.resolver
except:
    print "Error: Please install dnspython library to resolver:"
    print "       pip install dnspython"
    sys.exit(1)


class LswCloudStackOps(CloudStackOps, LswCloudStackOpsBase):

    MGMT_SERVER = None
    MGMT_SERVER_DATA = {}

    # Caches
    alarmedInstancesCache = {}
    alarmedRoutersCache = {}
    routerTemplateId = None

    ENABLED_CHECKS_HOST = []

    # Internal attributes
    _ssh = None
    _filters = {}

    # Init function
    def __init__(self, debug=0, dryrun=0, force=0):
        self.apikey = ''
        self.secretkey = ''
        self.api = ''
        self.cloudstack = ''
        self.DEBUG = debug
        self.DRYRUN = dryrun
        self.FORCE = force
        self.configProfileNameFullPath = ''
        self.apiurl = ''
        self.apiserver = ''
        self.apiprotocol = ''
        self.apiport = ''
        self.csApiClass = ''
        self.conn = ''
        self.organization = ''
        self.smtpserver = 'localhost'
        self.mail_from = ''
        self.errors_to = ''
        self.configfile = os.getcwd() + '/config'
        self.pp = pprint.PrettyPrinter(depth=6)
        self.ssh = None
        self.xenserver = None

        self.printWelcome()
        self.checkScreenAlike()

        self.ENABLED_CHECKS_HOST += [ 'io-abuse' ]
        self.ENABLED_CHECKS_HOST += [ 'conntrack' ]
        self.ENABLED_CHECKS_HOST += [ 'load-avg' ]

        signal.signal(signal.SIGINT, self.catch_ctrl_C)


    def retrieveRouterTemplateId(self):
        # One of the router tests we can make is to assess it's template version, if it's 
        # current to the Global Setting router.template.kvm, so let's fetch that one.
        confrtpl = self.getConfiguration("router.template.kvm" )
        routerTemplateName = confrtpl[0].value
        # Watch out for the use of "keyword". It should be "name", but Marvin API returns more results than expected..
        routerTemplateData = self.listTemplates({'templatefilter': 'all', 'keyword': routerTemplateName, 'listall': 'True'})

        if type(routerTemplateData) is not list:
            print "ERROR: Failed to acquire the current router template"
            sys.exit(2)

        for r in routerTemplateData:
            if r.name == routerTemplateName:
                self.routerTemplateId = r.id

        if self.routerTemplateId == None:
            print "WARNING: Could not find the 'router.template.kvm' setting (name: %s)" % (routerTemplateName)
            return False

        return True

    def assignSshObject(self, ssh):
        self._ssh = ssh

    def setManagementServer(self, srv):
        self.MGMT_SERVER = srv

    def setFilter(self, filter, value):
        self._filters[filter] = value

    def setFilters(self, arg):
        self._filters = arg

    def getFilter(self, filter):
        if filter in self._filters:
            return self._filters[filter]
        return False

    def examineHost(self, host):
        def getHostIp(host):
            # Unfortunatel, cs00 used FQDNs in the node name
            if host.name.find('.')!=-1:
               (hostname, hostdomain) = host.name.split('.', 1)
               host.name = hostname
            return host.name + "." + PLATFORM

        
        self.debug(2, ' + Checking agent connection state: ' + host.state)
        if host.state != 'Up':
            return { 'action': LswCloudStackOpsBase.ACTION_MANUAL, 'safetylevel': LswCloudStackOpsBase.SAFETY_NA, 'comment': 'Agent is not Up (' + host.state + ')' }

        nodeversion = self.normalizePackageVersion(host.version)
        self.debug(2, ' + Comparing h.version(%s) with MGMT version(%s)' % (nodeversion, self.MGMT_SERVER_DATA['version.normalized']))
        if nodeversion != self.MGMT_SERVER_DATA['version.normalized']:
            return { 'action': LswCloudStackOpsBase.ACTION_MANUAL, 'safetylevel': LswCloudStackOpsBase.SAFETY_NA, 'comment': 'Agent version mistatch (' + host.version + '!=' + self.MGMT_SERVER_DATA['version'] + ')' }

        nodeSrv = getHostIp(host)
        if self._ssh:
            adv = self._ssh.examineHost(nodeSrv)
            if adv:
                return adv

        return { 'action': None, 'safetylevel': LswCloudStackOpsBase.SAFETY_NA, 'comment': '' }


    def getAdvisoriesHosts(self):
        self.debug(2, "getAdvisoriesHosts : begin")
        results = []
        
        hostData = self.getHostData({'type': 'Routing'})
        for host in hostData:
            self.debug(2, "Processing: host.name = %s, type = %s" % (host.name, host.type))

            diag = examineHost(host)
            if self.getFilter('all') or (diag['action'] != None):
                results += [{ 'id': host.id, 'name': host.name, 'domain': 'ROOT', 'asset_type': 'host', 'adv_action': diag['action'], 'adv_safetylevel': diag['safetylevel'], 'adv_comment': diag['comment']}]
     
        self.debug(2, "getAdvisoriesHosts : end")
        return results





    def getAdvisoriesInstances(self):
        self.debug(2, "getAdvisoriesInstances : begin")
        results = []
        
        for i_name in self.alarmedInstancesCache.keys():
            i_domain = ''
            i_id = ''
            iinfo = self.listVirtualmachines({'instancename': i_name})
            
            #import pprint
            #pp = pprint.PrettyPrinter(indent=4)
            #pp.pprint(iinfo)
            #break;

            for i in iinfo:
                if i.instancename == i_name:
                    i_domain = i.domain
                    i_id = i.id
            if self.alarmedInstancesCache[i_name]['alarm']=='read-only':
                comment = 'Instance is reported read-only'
                action = LswCloudStackOpsBase.ACTION_MANUAL
                safety = LswCloudStackOpsBase.SAFETY_NA
            elif alarmedInstancesCache[i_name]['alarm']=='io-abuse':
                comment = 'Instance is abusing I/O (' + ','.join(self.alarmedInstancesCache[i_name]['metrics']) + ')'
                action = LswCloudStackOpsBase.ACTION_I_THROTTLE
                safety = LswCloudStackOpsBase.SAFETY_NA
            # else should never happen #
            
            results += [{ 'id': i_id, 'name': i_name, 'domain': i_domain, 'asset_type': 'instance', 'adv_action': action, 'adv_safetylevel': safety, 'adv_comment': comment}]

        self.debug(2, "getAdvisoriesInstances : end")
        return results

    def getAdvisoriesNetworks(self):
        self.debug(2, "getAdvisoriesNetworks/Routers : begin")
        
        results = []

        # This method will analyse the network and return an advisory
        def examineNetwork(network, advRouters, redundantstate):
            if network.restartrequired:
                if network.rr_type:
                    return {'action': LswCloudStackOpsBase.ACTION_N_RESTART, 'safetylevel': LswCloudStackOpsBase.SAFETY_BEST, 'comment': 'Restart flag on, redundancy present'}
                else:
                     if network.type == 'Shared':
                         return {'action': LswCloudStackOpsBase.ACTION_N_RESTART, 'safetylevel': LswCloudStackOpsBase.SAFETY_GOOD, 'comment': 'Restart flag on, no redundancy (Shared network)'}
                     else:
                         return {'action': LswCloudStackOpsBase.ACTION_N_RESTART, 'safetylevel': LswCloudStackOpsBase.SAFETY_DOWNTIME, 'comment': 'Restart flag on, no redundancy'}

            self.debug(2, '   + check redundantstate (rr_type: ' + str(network.rr_type) + ') => ' + str(redundantstate) )
            # Only look at the combined redundantstate if at least one router was found Running
            if ((redundantstate['flags']&4) and ((redundantstate['flags'] & 3)!=3)):
                return {'action': LswCloudStackOpsBase.ACTION_N_RESTART, 'safetylevel': LswCloudStackOpsBase.SAFETY_UNKNOWN, 'comment': 'Redundancy state is found inconsistent (' + ','.join(redundantstate['states']) + ')'}

            if len(advRouters)>0:
                rnames = [];
                for r in advRouters:
                    rnames = rnames + [ r['name'] ];
                if network.rr_type:
                    return {'action': LswCloudStackOpsBase.ACTION_N_RESTART, 'safetylevel': LswCloudStackOpsBase.SAFETY_BEST, 'comment': 'Network tainted (State:' + network.state + '), problems found with router(s): ' + ','.join(rnames)}
                else:
                    if network.type == 'Shared':
                        return {'action': LswCloudStackOpsBase.ACTION_N_RESTART, 'safetylevel': LswCloudStackOpsBase.SAFETY_GOOD, 'comment': 'Network tainted (State:' + network.state + '), problems found with router(s): ' + ','.join(rnames)}
                    else:
                        return {'action': LswCloudStackOpsBase.ACTION_N_RESTART, 'safetylevel': LswCloudStackOpsBase.SAFETY_DOWNTIME, 'comment': 'Network tainted (State:' + network.state + '), problems found with router(s): ' + ','.join(rnames)}
                
            return {'action': None, 'safetylevel': LswCloudStackOpsBase.SAFETY_NA, 'comment': ''}

        # Use this when you want to inspect routers real-time
        # Note: Development was dropped in favor of examineRouterInternalsQuick()
        # TODO we should provide a --deep switch
        def examineRouterInternalsLive(router):
            self.debug(2, "   + router: name: %s, ip=%s, host=%s, tpl=%s" % (router.name, router.linklocalip, router.hostname, (router.version if router.templateversion==None else router.templateversion)))

            # Use the cache anyway, to mark already checked routers:
            if router.name in self.alarmedRoutersCache.keys():
                if self.alarmedRoutersCache[router.name]['checked']:
                    return 0, None

            #mgtSsh = "ssh -At %s ssh -At -p 3922 -i /root/.ssh/id_rsa.cloud -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no root@%s ls -la" % (router.hostname, router.linklocalip)
            if self.MGMT_SERVER_DATA['dist'] == 'RedHat':
                mgtSsh = "/usr/local/bin/check_routervms.sh " + router.name
            else:
                mgtSsh = "/usr/local/bin/check_routervms.py " + router.name

            self.debug(2, "     + cmd: " + mgtSsh)
            retcode, output = self._ssh.runSSHCommand(self.MGMT_SERVER, mgtSsh)
            if retcode != 0:
                return 256, "check_routervms.py returned errors"

            if self.MGMT_SERVER_DATA['dist'] == 'Debian':                
                lines = output.split('\n')
                retcode = int(lines[-1])
                output = "check_routervms returned errors"
                self.debug(2, "       + retcode=%d" % (retcode))

            # Use the cache anyway, to mark already checked routers:
            self.alarmedRoutersCache[router.name] = { 'network': router.network, 'code': retcode, 'checked': True }

            return retcode, output

        def examineRouterInternalsQuick(router):
            self.debug(2, "   + router: name: %s, ip=%s, host=%s, tpl=%s" % (router.name, router.linklocalip, router.hostname, (router.version if router.templateversion==None else router.templateversion)))

            if router.name in self.alarmedRoutersCache.keys():
                if not self.alarmedRoutersCache[router.name]['checked']:
                    self.alarmedRoutersCache[router.name]['checked'] = True
                    return self.alarmedRoutersCache[router.name]['code'], "check_routervms returned errors"

            return 0, None
            
        def resolveRouterErrorCode(errorCode):
            str = []
            errorCode = int(errorCode)
            if errorCode & 1:
                str = str + [ 'dmesg' ]
            if errorCode & 2:
                str = str + [ 'swap' ]
            if errorCode & 4:
                str = str + [ 'resolver' ]
            if errorCode & 8:
                str = str + [ 'ping' ]
            if errorCode & 16:
                str = str + [ 'filesystem' ]
            if errorCode & 32:
                str = str + [ 'disk' ]
            if errorCode & 64:
                str = str + [ 'password' ]
            if errorCode & 128:
                str = str + [ 'reserved' ]
            if errorCode & 256:
                str = str + [ 'check_routervms.py' ]
            return ",".join(str)

        def getActionForStatus(statuscode, router, rr_type, net_type):
            if int(statuscode) & 4:
                if rr_type:
                    return LswCloudStackOpsBase.ACTION_ESCALATE, LswCloudStackOpsBase.SAFETY_BEST
                else:
                    if net_type == 'Shared':
                        return LswCloudStackOpsBase.ACTION_ESCALATE, LswCloudStackOpsBase.SAFETY_GOOD
                    else:
                        return LswCloudStackOpsBase.ACTION_ESCALATE, LswCloudStackOpsBase.SAFETY_DOWNTIME
            if int(statuscode) & 8:
                if rr_type:
                    return LswCloudStackOpsBase.ACTION_ESCALATE, LswCloudStackOpsBase.SAFETY_BEST
                else:
                    if net_type == 'Shared':
                        return LswCloudStackOpsBase.ACTION_ESCALATE, LswCloudStackOpsBase.SAFETY_GOOD
                    else:
                        return LswCloudStackOpsBase.ACTION_ESCALATE, LswCloudStackOpsBase.SAFETY_DOWNTIME
            if int(statuscode) & 32:
                return LswCloudStackOpsBase.ACTION_R_LOG_CLEANUP, LswCloudStackOpsBase.SAFETY_BEST
            if int(statuscode) & 64:
                return LswCloudStackOpsBase.ACTION_R_RST_PASSWD_SRV, LswCloudStackOpsBase.SAFETY_BEST
            return LswCloudStackOpsBase.ACTION_UNKNOWN, LswCloudStackOpsBase.SAFETY_UNKNOWN

        def examineRouter(network, router):
            if router.requiresupgrade:
                if network.rr_type:
                    return {'action': LswCloudStackOpsBase.ACTION_ESCALATE, 'safetylevel': LswCloudStackOpsBase.SAFETY_BEST, 'comment': 'Redundancy requires upgrade, redundancy present'}
                else:
                    return {'action': LswCloudStackOpsBase.ACTION_ESCALATE, 'safetylevel': LswCloudStackOpsBase.SAFETY_DOWNTIME, 'comment': 'Redundancy requires upgrade, no redundancy'}
            
            if router.isredundantrouter and (router.redundantstate not in ['MASTER', 'BACKUP']):
                if network.rr_type:
                    return {'action': LswCloudStackOpsBase.ACTION_ESCALATE, 'safetylevel': LswCloudStackOpsBase.SAFETY_BEST, 'comment': 'Redundancy state broken (' + router.redundantstate + '), redundancy present'}
                else:
                    return {'action': LswCloudStackOpsBase.ACTION_ESCALATE, 'safetylevel': LswCloudStackOpsBase.SAFETY_DOWNTIME, 'comment': 'Redundancy state broken (' + router.redundantstate + '), no redundancy'}

            # We should now try to assess the router internal status (with SSH)
            #retcode, output = examineRouterInternals(router)
            if self.getFilter('live'):
                retcode, output = examineRouterInternalsLive(router)
            else:
                retcode, output = examineRouterInternalsQuick(router)

            if retcode != 0:
                action, safetylevel = getActionForStatus(retcode, router, network.rr_type, network.type)
                return {'action': action, 'safetylevel': safetylevel, 'comment': output + ": " + str(retcode) + " (" + resolveRouterErrorCode(retcode) + ")" }

            # We now assess if the router VM template is current
            if self.getFilter('deep') and (self.routerTemplateId!=None):
                if router.templateid != self.routerTemplateId:
                    if network.rr_type:
                        return {'action': LswCloudStackOpsBase.ACTION_ESCALATE, 'safetylevel': LswCloudStackOpsBase.SAFETY_BEST, 'comment': 'Router using obsolete template, redundancy present'}
                    else:
                        if network.type == 'Shared':
                            return {'action': LswCloudStackOpsBase.ACTION_ESCALATE, 'safetylevel': LswCloudStackOpsBase.SAFETY_GOOD, 'comment': 'Router using obsolete template, redundancy not critical'}
                        else:
                            return {'action': LswCloudStackOpsBase.ACTION_ESCALATE, 'safetylevel': LswCloudStackOpsBase.SAFETY_DOWNTIME, 'comment': 'Router using obsolete template, no redundancy'}

            # be nice with (really old) legacy
            if not hasattr(router, 'cloudstackversion') or (router.cloudstackversion==None):
                router.cloudstackversion = ''
            rversion = self.normalizePackageVersion(router.cloudstackversion)
            if self.getFilter('deep') and (router.cloudstackversion):
                rversion = self.normalizePackageVersion(router.cloudstackversion)
                # If the router version is more recent than the package, then there probably emergency patching was made
                # so, it's not really a problem, nor suprising.
                if self.csVersionCompare(rversion,self.MGMT_SERVER_DATA['version.normalized'])<0:
                    if network.rr_type:
                        return {'action': LswCloudStackOpsBase.ACTION_ESCALATE, 'safetylevel': LswCloudStackOpsBase.SAFETY_BEST, 'comment': 'Router in deprecated version, redundancy presentt'}
                    else:
                        if network.type == 'Shared':
                            return {'action': LswCloudStackOpsBase.ACTION_ESCALATE, 'safetylevel': LswCloudStackOpsBase.SAFETY_GOOD, 'comment': 'Router in deprecated version, redundancy not critical'}
                        else:
                            return {'action': LswCloudStackOpsBase.ACTION_ESCALATE, 'safetylevel': LswCloudStackOpsBase.SAFETY_DOWNTIME, 'comment': 'Router in deprecated version, no redundancy'}
                    

            return {'action': None, 'safetylevel': LswCloudStackOpsBase.SAFETY_NA, 'comment': ''}


        networkData = self.listNetworks({})
        for network in networkData:
            self.debug(2, " + Processing: network.name = %s (%s)" % (network.name, network.state))

            if network.vpcid:
                self.debug(2, '   + Skipped, sub-network analysis will done done at the VPC level (later)')
                continue
            
            network.rr_type = False
            net_type = network.type

            if network.service:
                for netsvc in network.service:
                    if netsvc.capability:
                        for cap in netsvc.capability:
                            if cap.name == 'RedundantRouter':
                                if cap.value == 'true':
                                    network.rr_type = True

            escalated = []
            redundantstate = { 'flags': 0, 'states': [] }
            if self.getFilter('routers'):
                # A network in state Allocated probably has routers Offline.
                # In that case, redundancystate will always be UNKNOWN or even unexplicable states
                if network.state != LswCloudStackOpsBase.STATE_ALLOCATED:
                    routersData = self.getRouterData({'networkid': network.id})
                    if routersData:
                        for r in routersData:
                            # redundantstate consistency has to be out since examineRouter only can analyse individual routers
                            if r.isredundantrouter:
                                if r.redundantstate == 'BACKUP':
                                   redundantstate['flags'] = redundantstate['flags'] | 1
                                elif r.redundantstate == 'MASTER':
                                   redundantstate['flags'] = redundantstate['flags'] | 2
                                redundantstate['states'] = redundantstate['states'] + [ r.redundantstate ]

                            # Examining a non-Running router can generate false results, as ACS is not operating it anymore
                            if r.state == 'Running':
                                # We note down that at least one router was found online, so we can consider the 
                                # redundantstate to be meaningfull
                                if r.state == 'Running':
                                   redundantstate['flags'] = redundantstate['flags'] | 4
                                diag = examineRouter(network, r)
                            else:
                                diag = {'action': None, 'safetylevel': LswCloudStackOpsBase.SAFETY_NA, 'comment': 'RouterVM is not Running. Skipped'}

                            if ( self.getFilter('all') or (diag['action'] != None) ):
                                if diag['action'] == LswCloudStackOpsBase.ACTION_ESCALATE:
                                    escalated = escalated + [{ 'id': r.id, 'name': r.name, 'domain': network.domain, 'asset_type': 'router', 'adv_action': diag['action'], 'adv_safetylevel': diag['safetylevel'], 'adv_comment': diag['comment'] }]
                                results = results + [{ 'id': r.id, 'name': r.name, 'domain': network.domain, 'asset_type': 'router', 'adv_action': diag['action'], 'adv_safetylevel': diag['safetylevel'], 'adv_comment': diag['comment'] }]

            if not network.rr_type or not self.getFilter('routers') or (network.state!='Implemented'):
                # silence redundantstate check
                redundantstate['flags'] = redundantstate['flags'] | 1 | 2

            diag = examineNetwork(network, escalated, redundantstate)
            if ( self.getFilter('networks') and (self.getFilter('all') or (diag['action'] != None)) ):
                results = results + [{ 'id': network.id, 'type': net_type, 'name': network.name, 'domain': network.domain, 'rr_type': network.rr_type, 'restartrequired': network.restartrequired, 'state': network.state, 'asset_type': 'network', 'adv_action': diag['action'], 'adv_safetylevel': diag['safetylevel'], 'adv_comment': diag['comment'] }]

        vpcData = self.listVPCs({})
        for vpc in vpcData:
            self.debug(2, " + Processing: vpc.name = %s (%s)" % (vpc.name, vpc.state))
            vpc.rr_type = False
            if vpc.redundantvpcrouter:
                vpc.rr_type = vpc.redundantvpcrouter

            escalated = []
            redundantstate = { 'flags': 0, 'states': [] }
            if self.getFilter('routers'):
                routersData = self.getRouterData({'vpcid': vpc.id})
                if routersData:
                    for r in routersData:
                        # redundantstate consistency has to be out since examineRouter only can analyse individual routers
                        if r.isredundantrouter:
                            if r.redundantstate == 'BACKUP':
                                redundantstate['flags'] = redundantstate['flags'] | 1
                            elif r.redundantstate == 'MASTER':
                                redundantstate['flags'] = redundantstate['flags'] | 2
                            redundantstate['states'] = redundantstate['states'] + [ r.redundantstate ]

                        # Examining a non-Running router can generate false results, as ACS is not operating it anymore
                        if r.state == 'Running':
                            # We note down that at least one router was found online, so we can consider the 
                            # redundantstate to be meaningfull
                            if r.state == 'Running':
                               redundantstate['flags'] = redundantstate['flags'] | 4
                            diag = examineRouter(vpc, r)
                        else:
                            diag = {'action': None, 'safetylevel': LswCloudStackOpsBase.SAFETY_NA, 'comment': 'RouterVM is not Running. Skipped'}

                        # We include 'escalate' in case opFilterNetworks is not set, to notify that we need it
                        # in order to fix this
                        if ( self.getFilter('all') or (diag['action'] != None) ):
                            if diag['action'] == LswCloudStackOpsBase.ACTION_ESCALATE:
                                escalated = escalated + [{ 'id': r.id, 'name': r.name, 'domain': vpc.domain, 'asset_type': 'router', 'adv_action': diag['action'], 'adv_safetylevel': diag['safetylevel'], 'adv_comment': diag['comment'] }]
                            results = results + [{ 'id': r.id, 'name': r.name, 'domain': vpc.domain, 'asset_type': 'router', 'adv_action': diag['action'], 'adv_safetylevel': diag['safetylevel'], 'adv_comment': diag['comment'] }]


            if not vpc.rr_type or not self.getFilter('routers'):
                # silence redundantstate check
                redundantstate['flags'] = redundantstate['flags'] | 1 | 2

            diag = examineNetwork(vpc, escalated, redundantstate)
            if ( self.getFilter('networks') and (self.getFilter('all') or (diag['action'] != None)) ):
                results = results + [{ 'id': vpc.id, 'type': 'VPC', 'name': vpc.name, 'domain': vpc.domain, 'rr_type': vpc.rr_type, 'restartrequired': vpc.restartrequired, 'state': vpc.state, 'asset_type': 'vpc', 'adv_action': diag['action'], 'adv_safetylevel': diag['safetylevel'], 'adv_comment': diag['comment'] }]
        
        self.debug(2, "getAdvisoriesNetworks/Routers : end")

        return results



    def getAdvisoriesSystemVMs(self):
        self.debug(2, "getAdvisoriesSystemVMs : begin")
        results = []
        
        def resolveSystemVMErrorCode(errorCode):
            str = []
            errorCode = int(errorCode)
            if errorCode & 1:
                str = str + [ 'dmesg' ]
            if errorCode & 2:
                str = str + [ 'swap' ]
            if errorCode & 4:
                str = str + [ 'resolver' ]
            if errorCode & 8:
                str = str + [ 'ping' ]
            if errorCode & 16:
                str = str + [ 'filesystem' ]
            if errorCode & 32:
                str = str + [ 'disk' ]
            if errorCode & 64:
                str = str + [ 'websockify' ]
            if errorCode & 128:
                str = str + [ 'reserved' ]
            if errorCode & 256:
                str = str + [ 'check_appliance.py' ]
            return ",".join(str)

        def examineSystemVMInternalsLive(svm):
            self.debug(2, "   + svm: name: %s, ip=%s, host=%s, tpl=%s" % (svm.name, svm.linklocalip, svm.hostname, (svm.version if svm.templateversion==None else svm.templateversion)))

            mgtSsh = "/usr/local/bin/check_appliance.py " + svm.name
            retcode, output = self._ssh.runSSHCommand(self.MGMT_SERVER, mgtSsh)
            if retcode != 0:
                return 256, "check_appliance.py returned errors"
                
            lines = output.split('\n')
            retcode = int(lines[-1])
            output = "check_appliance returned errors"
            self.debug(2, "   + cmd: " + mgtSsh)
            self.debug(2, "       + retcode=%d" % (retcode))

            return retcode, output

        def getActionForStatus(statuscode, svm):
            # If the helper scripts do not exist at the mgt server, the call returns exit code 256.
            if statuscode==256:
                return LswCloudStackOpsBase.ACTION_ESCALATE, LswCloudStackOpsBase.SAFETY_NA

            return LswCloudStackOpsBase.ACTION_S_DESTROY, LswCloudStackOpsBase.SAFETY_GOOD

        def examineSystemVMInternals(svm):

            # We now assess if the router VM template is current
            if (self.getFilter('deep')) and (self.routerTemplateId!=None):
                if svm.templateid != self.routerTemplateId:
                    self.debug(2, ' + SystemvM using obsolete template: sysvm=' + svm.templateid + ' != r.t.kvm=' + self.routerTemplateId)
                    return {'action': LswCloudStackOpsBase.ACTION_S_DESTROY, 'safetylevel': LswCloudStackOpsBase.SAFETY_GOOD, 'comment': 'SystemvM using obsolete template' }

            # We should now try to assess the systemvm internal status (with SSH)
            retcode, output = examineSystemVMInternalsLive(svm)

            if retcode != 0:
                action, safetylevel = getActionForStatus(retcode, svm)
                return {'action': action, 'safetylevel': safetylevel, 'comment': output + ": " + str(retcode) + " (" + resolveSystemVMErrorCode(retcode) + ")" }

            return {'action': None, 'safetylevel': LswCloudStackOpsBase.SAFETY_NA, 'comment': ''}

        svmData = self.getSystemVmData({})
        for svm in svmData:
            print "name=" + svm.name + ", type=" + svm.systemvmtype
            
            svmtype = '????'
            if svm.systemvmtype=='consoleproxy':
                svmtype = 'cpvm'
            elif svm.systemvmtype=='secondarystoragevm':
                svmtype = 'ssvm'

            # We should now try to assess the systemvm internal status (with SSH)
            diag = examineSystemVMInternals(svm)
            
            if ( self.getFilter('all') or (diag['action'] != None) ):
                results = results + [{ 'id': svm.id, 'name': svm.name, 'domain': svm.domain, 'asset_type': svmtype, 'adv_action': diag['action'], 'adv_safetylevel': diag['safetylevel'], 'adv_comment': diag['comment'] }]

        self.debug(2, "getAdvisoriesSystemVMs : end")
        return results




    def setAlarmedRoutersCache(self, cache):
        self.alarmedRoutersCache = cache
        return True

    def setAlarmedInstancesCache(self, cache):
        self.alarmedInstancesCache = cache
        return True

    def getAdvisoriesConfiguration(self):
        results = []
        self.debug(2, "getAdvisoriesConfiguration : begin")

        my_resolver = dns.resolver.Resolver()

        zonedata = self.listZones({})
        for zone in zonedata:
            for dnsx in ['dns1', 'dns2']:
                if hasattr(zone, dnsx):
                    my_resolver.nameservers = [ getattr(zone, dnsx) ]
                    try:
                        dns.exception.Timeout = 3
                        answer = my_resolver.query('www.google.com')
                    except:
                        results = results + [{ 'id': zone.id, 'name': zone.name, 'domain': '-', 'asset_type': 'config', 'adv_action': LswCloudStackOpsBase.ACTION_MANUAL, 'adv_safetylevel': LswCloudStackOpsBase.SAFETY_BEST, 'adv_comment': 'Zone \'%s\' has parameter \'%s\' with invalid value: %s' % (zone.name, dnsx, getattr(zone, dnsx)) }]

        self.debug(2, "getAdvisoriesConfiguration : end")
        return results

    def getAdvisories(self):
            
        self.retrieveRouterTemplateId()

        self.MGMT_SERVER_DATA = self._ssh.testMgmtServerConnection(self.MGMT_SERVER)

        results = []
        if self.getFilter('networks') or self.getFilter('routers'):
            self.setAlarmedRoutersCache( self._ssh.retrieveAlarmedRoutersCache(self.MGMT_SERVER) )
            results = results + self.getAdvisoriesNetworks()
        if self.getFilter('instances') or self.getFilter('hosts'):
            self.setAlarmedInstancesCache( self._ssh.retrieveAlarmedInstancesCache(self.MGMT_SERVER) )
        if self.getFilter('hosts'):
            results = results + self.getAdvisoriesHosts()
        if self.getFilter('instances'):
            results = results + self.getAdvisoriesInstances()
        if self.getFilter('resources'):
            results = results + self._ssh.getAdvisoriesResources(self.MGMT_SERVER)
        if self.getFilter('systemvms'):
            results = results + self.getAdvisoriesSystemVMs()
        if self.getFilter('config'):
            results = results + self.getAdvisoriesConfiguration()

        def getSortKey(item):
            return item['asset_type'].upper() + '-' + item['name'].upper() 

        # Filter out advisories not in the specified safety level, if set
        newResults = []
        for r in results:
            if (not self.getFilter('safetylevel_set')) or (r['adv_safetylevel']==self.getFilter('safetylevel')):
                newResults = newResults + [ r ]

        return sorted(newResults, key=getSortKey)

    def repairRouter(self, adv):
        self.debug(2, "repairRouter(): router:%s, action:%s" % (adv['name'], adv['adv_action']))
        if (self.DRYRUN==1) and (adv['adv_action'] in [LswCloudStackOpsBase.ACTION_R_RST_PASSWD_SRV, LswCloudStackOpsBase.ACTION_R_LOG_CLEANUP]):
            return -2, 'Skipping, dryrun is on.'

        if adv['adv_action'] == LswCloudStackOpsBase.ACTION_ESCALATE:
            return -2, 'Escalated'

        if adv['adv_action'] == None:
            return -2, ''

        if not self._ssh:
            return -2, 'No SSH client set'

        if adv['adv_action'] == LswCloudStackOpsBase.ACTION_R_RST_PASSWD_SRV:
            mgtSsh = '/usr/local/bin/routervm_ssh.sh ' + adv['name'] + ' /etc/init.d/cloud-passwd-srvr restart'
            retcode, output = self._ssh.runSSHCommand(self.MGMT_SERVER, mgtSsh)
            if retcode==0:
                output = 'cloud-passwd-srvr restarted'
            return retcode, output
        if adv['adv_action'] == LswCloudStackOpsBase.ACTION_R_LOG_CLEANUP:
            mgtSsh = '/usr/local/bin/routervm_ssh.sh ' + adv['name'] + " '/usr/bin/find /var/log -mtime +2 -type f -exec rm -f \\{\\} \\\\\;'"
            retcode, output = self._ssh.runSSHCommand(self.MGMT_SERVER, mgtSsh)
            if retcode==0:
                output = 'tried deleted -mtime +2 files'
            return retcode, output

        return -1, 'Not implemented'

    def repairNetwork(self, adv):
        self.debug(2, "repairNetwork(): network:%s, action:%s" % (adv['name'], adv['adv_action']))
        
        if adv['adv_action'] == None:
            return -2, ''

        if adv['adv_action']==LswCloudStackOpsBase.ACTION_N_RESTART:
            if self.getFilter('safetylevel')==adv['adv_safetylevel']:
                self.debug(2, ' + restart network.name=%s, .id=%s' % (adv['name'], adv['id']))
                if self.DRYRUN==1:
                    return -2, 'Skipping, dryrun is on.'
                if adv['asset_type']=='network':
                    print "Restarting network '%s'" % (adv['name'])
                    ret = self.restartNetwork(adv['id'], True)
                elif adv['asset_type']=='vpc':
                    print "Restarting vpc '%s'" % (adv['name'])
                    ret = self.restartVPC(adv['id'], True)
                self.debug(2, " + ret = " + str(ret))
                if ret and (hasattr(ret, 'success')) and (ret.success == True):
                    return 0, 'Network restarted without errors.'
                else:
                    return 1, 'Errors during the restart. Check messages above.'
            else:
                return -2, 'Ignored by SafetyLevel scope (' + LswCloudStackOpsBase.translateSafetyLevel( self.getFilter('safetylevel') ) + ')'

        return -1, 'Not implemented'

    def repairSystemVM(adv):
        self.debug(2, "repairSystemVM(): systemvm:%s, action:%s" % (adv['name'], adv['adv_action']))
        
        if adv['adv_action'] == None:
            return -2, ''

        if adv['adv_action']==LswCloudStackOpsBase.ACTION_S_DESTROY:
            if self.getFilter('safetylevel')==adv['adv_safetylevel']:
                self.debug(2, ' + destroy systemvm.name=%s, .id=%s' % (adv['name'], adv['id']))
                if self.DRYRUN==1:
                    return -2, 'Skipping, dryrun is on.'
                print "Destroying systemVM '%s'" % (adv['name'])
                ret = self.destroySystemVM(adv['id'])
                self.debug(2, " + ret = " + str(ret))
                
                return 0, 'SystemVM destroyed without errors.'
            else:
                return -2, 'Ignored by SafetyLevel scope (' + LswCloudStackOpsBase.translateSafetyLevel(SAFETYLEVEL) + ')'

        return -1, 'Not implemented'


    def runRepair(self):
        self.debug(2, "runRepair : begin")
        results = self.getAdvisories()
        self.debug(2, " + found %d results" % (len(results)))
        for adv in results:
            if self.getFilter('routers') and (adv['asset_type'] == 'router'):
                applied,output = self.repairRouter(adv)
            if self.getFilter('networks') and (adv['asset_type'] in ['network', 'vpc']):
                applied, output = self.repairNetwork(adv)
            if self.getFilter('systemvms') and (adv['asset_type'] in ['cpvm', 'ssvm']):
                applied, output = self.repairSystemVM(adv)
            if applied==0:
                adv['repair_code'] = 'OK'
                adv['repair_msg'] = 'Repair successful: ' + output
            elif applied>0:
                adv['repair_code'] = 'NOK'
                adv['repair_msg'] = 'Repair unsuccesful: ' + output
            else:
                adv['repair_code'] = 'N/A'
                adv['repair_msg'] = output

        self.debug(2, "runRepair : end")
        return results
