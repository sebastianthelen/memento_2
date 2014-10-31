from flask import Flask
from flask import request
from flask import redirect
from flask import make_response
from email.utils import formatdate
from wsgiref.handlers import format_date_time
import flask
import time
import requests
import json
import logging
import logging.handlers
import email.utils as eut
import datetime

# suppress logging messages from requests lib
requests_log = logging.getLogger("requests")
requests_log.setLevel(logging.ERROR)

# global variable stores uri of original resource uthread safe???!?!
uri_g = None

app = Flask(__name__)

# compute original resource (URI-G) in a hierarchy
URI_G_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select distinct ?predecessor where { '
    '?predecessor cdm:complex_work_has_member_work* <%(uri)s>. '
    '?predecessor ?p ?o. '
    'filter not exists{?anotherWork cdm:complex_work_has_member_work ?predecessor.}} '
)

# determine in which dimension an evolutive work should
# perform datetime negotiation in (indicated by cdm:datetime_negotiation)
DATETIME_PROPERTY_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select distinct ?prop where {<%(uri)s> cdm:datetime_negotiation ?prop.}'
)

# compute location information of next redirect based on
# current uri and accept-datetime parameter
LOCATION_TEMPLATE = (
    "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
    "SELECT distinct ?successor (bif:datediff( 'minute', xsd:dateTime(str(?date)) ,'%(accept_datetime)s'^^xsd:dateTime) as ?diff_date) "
    "WHERE "
    "{ "
    "<%(uri)s> cdm:datetime_negotiation ?datetime_property; "
    "cdm:complex_work_has_member_work+ ?individual_work; "
    "cdm:complex_work_has_member_work ?successor. "
    "?successor cdm:complex_work_has_member_work? ?individual_work. "
    "?individual_work ?datetime_property ?date. "
    "FILTER (xsd:dateTime(?date) <= '%(accept_datetime)s'^^xsd:dateTime) "
    "} "
    "ORDER BY ASC(?diff_date) "
    "LIMIT 1 "
)

# perform sparql describe query for given uri
DESCRIBE_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'DESCRIBE <%(uri)s> '
)

# test whether given work is instance of cdm:complex_work
COMPLEX_WORK_TEMPLATE = (
    'define input:inference "cdm_rule_set" '
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'ASK { <%(uri)s> a <http://publications.europa.eu/ontology/cdm#complex_work>.}'
)

# return memento datetime of given resource (corresponds to
# cdm:work_date_document)
MEMENTO_DATETIME_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select ?date '
    'where '
    '{<%(uri)s> cdm:work_date_creation ?date.}'
)

# return related complex works
RELATED_COMPLEX_WORKS = (
    'define input:inference "cdm_rule_set" '
    'prefix cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select distinct ?complex_work where {'
    '<%(uri)s> cdm:complex_work_has_member_work|^cdm:complex_work_has_member_work ?complex_work.'
    '?complex_work a cdm:complex_work. }'
)
# return related mementos together with their memento-datetime
RELATED_MEMENTOS= (
    'prefix cdm: <http://publications.europa.eu/ontology/cdm#> ' 
    'select distinct ?memento ?date where {'
    '<%(uri)s> cdm:complex_work_has_member_work ?memento.'
    '?memento cdm:work_date_creation ?date.'
    'filter not exists { ?memento cdm:complex_work_has_member_work ?member.} }'
)

# return timeamp related information (startdate, enddate and type of date)
TIMEMAPINFO= (
    'define input:inference "cdm_rule_set" '
    'prefix cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select (min(?o) as ?startdate) (max(?o) as ?enddate) (?p as ?typeofdate) where {'
    '<%(uri)s> cdm:datetime_negotiation ?p;  '
    'cdm:complex_work_has_member_work ?member. '
    '?member ?p ?o.}'
)

def sparqlQuery(query, base_url, format="application/json"):
    """perform sparql query and return result"""
    payload = {
        "default-graph-uri": "",
        "query": query,
        "debug": "on",
        "timeout": "",
        "format": format
    }
    resp = requests.get(base_url, params=payload)
    return resp.text


@app.route('/memento/<id>')
def processMementoRequest(id=None):
    """process memento service request (non-information resources)"""
    response = None
    uri = "http://publications.europa.eu/resource/celex/" + id
    # return memento (target resource is not a complex work)
    if not(isComplexWork(uri)):
        response = mementoCallback(uri)
        return response
    query = URI_G_TEMPLATE % {'uri': uri}
    LOGGER.debug('URI_G_TEMPLATE: %s' % query)
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    global uri_g
    uri_g = json_obj['results']['bindings'][0]['predecessor']['value']
    LOGGER.debug("URI-G: %s" % uri_g)
    # uri matches a complex work and the rel parameter is set to 'timemap'
    if request.args.get('rel') == 'timemap':
        response = timemapCallback(uri)
    # uri matches a top level complex work (original timegate)
    elif uri_g == uri:
        response = originalTimegateCallback(uri)
    # uri matches a complex work but not the top level one
    else:
        response = timegateCallback(uri)
    return response

@app.route('/data/<id>')
def processDataRdfRequest(id=None):
    """process data representation request (information resources)"""
    LOGGER.debug('Processing data request ...')
    
    response = None
    uri = "http://publications.europa.eu/resource/celex/" + id
    if id.endswith('.txt'):
        response = dataRepresentationCallback(uri.replace('.txt',''), True)
    else:
        response = dataRepresentationCallback(uri.replace('.xml',''), False)
    return response


def originalTimegateCallback(uri_g):
    """processing logic when requesting an original resource"""
    LOGGER.debug('Executing originalResourceCallback...')
    accept_datetime = None
    location = None
    # redirect to intermediate resource
    if 'Accept-Datetime' in request.headers:
        accept_datetime = parseDate(request.headers['Accept-Datetime'])
        LOGGER.debug('Accept-Datetime: %s' % accept_datetime)
        # determine negotiation dimension
        datetime_property = determineDatetimeProperty(uri_g)
        # compute location infomation of redirect
        location = determineLocation(uri_g, accept_datetime)
    # redirect to most recent representation
    else:
        # current timestamp
        now = time.strftime("%Y-%m-%dT%XZ")
        location = uri_g
        # cascading selection of most recent representation
        while isComplexWork(location):
            location = determineLocation(location, now)
            if location == None:
                break
    # actually it is not Memento compliant to return an HTTP 406 here.
    # See section 4.5.3 of the specification
    if location == None:
        return make_response("Bad Request. Check your query parameters", 406)
    # link headers
    localhost_uri_g = 'localhost:5000/%s' % toLocalhostUri(uri_g)
    localhost_uri_t = 'localhost:5000/%s' % toLocalhostUri(
        uri_g + '?rel=timemap')
    LOGGER.debug('localhost_uri_g: %s' % localhost_uri_g)
    LOGGER.debug('localhost_uri_t: %s' % localhost_uri_t)
    # return redirection object
    redirect_obj = redirect(toLocalhostUri(location), code=302)
    redirect_obj.headers['Link'] = '<%(localhost_uri_g)s>; rel="original timegate", ' \
        '<%(localhost_uri_t)s>; rel="timemap"' % {
            'localhost_uri_g': localhost_uri_g, 'localhost_uri_t': localhost_uri_t}
    redirect_obj.headers['Vary'] = 'accept-datetime'
    return redirect_obj


def timegateCallback(uri):
    """processing logic when requesting an intermediate resource/timegate"""
    LOGGER.debug('Executing timegateCallback...')
    # default to now if no accept-datetime is provided
    accept_datetime = ('Accept-Datetime' in request.headers) and parseDate(request.headers[
        'Accept-Datetime']) or time.strftime("%Y-%m-%dT%XZ")
    # dimension of datetime negotiation
    datetime_property = determineDatetimeProperty(uri)
    # compute redirect
    location = determineLocation(uri, accept_datetime)
    if location == None:
        return make_response("Bad Request. Check your query parameters", 406)
    # link headers
    localhost_uri_g = 'localhost:5000/%s' % toLocalhostUri(uri_g)
    localhost_uri_t = 'localhost:5000/%s' % toLocalhostUri(
        uri_g + '?rel=timemap')
    LOGGER.debug('localhost_uri_g: %s' % localhost_uri_g)
    LOGGER.debug('localhost_uri_t: %s' % localhost_uri_t)
    # return redirection object
    redirect_obj = redirect(toLocalhostUri(location), code=302)
    redirect_obj.headers['Link'] = '<%(localhost_uri_g)s>; rel="original timegate", ' \
        '<%(localhost_uri_t)s>; rel="timemap"' % {
            'localhost_uri_g': localhost_uri_g, 'localhost_uri_t': localhost_uri_t}
    return redirect_obj


def mementoCallback(uri):
    """processing logic when requesting a memento"""
    LOGGER.debug('Executing mementoCallback...')
    describe_query = DESCRIBE_TEMPLATE % {'uri': uri}
    #LOGGER.debug('DESCRIBE_TEMPLATE: %s' % describe_query )
    describe = sparqlQuery(
        describe_query, 'http://abel:8890/sparql', format='text/html')
    memento_datemtime_query = MEMENTO_DATETIME_TEMPLATE % {'uri': uri}
    #LOGGER.debug('MEMENTO_DATETIME_TEMPLATE: %s' % memento_datemtime_query )
    json_str = sparqlQuery(memento_datemtime_query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    # link headers
    memento_datetime = None
    try:
        memento_datetime = json_obj['results']['bindings'][0]['date']['value']
    except:
        return make_response("Not found. Could not retrieve resource metadata", 404)
    localhost_uri_g = 'localhost:5000/%s' % toLocalhostUri(uri_g)
    localhost_uri_t = 'localhost:5000/%s' % toLocalhostUri(
        uri_g + '?rel=timemap')
    #response = make_response(describe, 200)
    response = redirect(toLocalhostDataUri(uri,'.xml'), code=303)
    response.headers['Memento-Datetime'] = stringToHTTPDate(memento_datetime)
    response.headers['Link'] = '<%(localhost_uri_g)s>; rel="original timegate", ' \
        '<%(localhost_uri_t)s>; rel="timemap"' % {
            'localhost_uri_g': localhost_uri_g, 'localhost_uri_t': localhost_uri_t}
    return response

def timemapCallback(uri):
    """processing logic when requesting a timemap"""
    LOGGER.debug('Executing timemapCallback...')
    localhost_uri_g = 'localhost:5000/%s' % toLocalhostUri(uri_g)
    redirect_obj = None
    if(request.headers['Accept'] == 'application/link-format'):
        redirect_obj = redirect(toLocalhostDataUri(uri,'.txt'), code=303)
    else:
        redirect_obj = redirect(toLocalhostDataUri(uri,'.xml'), code=303)
    redirect_obj.headers['Link'] = '<%(localhost_uri_g)s>; rel="original timegate"' % {
            'localhost_uri_g': localhost_uri_g}
    return redirect_obj
     
def dataRepresentationCallback(uri, linkformat):
    """processing logic when requesting a data representation (information resource)"""
    LOGGER.debug('Executing dataRepresentationCallback...')
    if linkformat:
        tm = generateLinkformat(uri)
        response = make_response(tm,200)
        response.headers['Content-Type'] = 'application/link-format; charset=utf-8'
    else:
        describe_query = DESCRIBE_TEMPLATE % {'uri': uri}
        describe = sparqlQuery(
            describe_query, 'http://abel:8890/sparql', format='application/rdf+xml')
        response = make_response(describe, 200)
        response.headers['Content-Type'] = 'application/rdf+xml; charset=utf-8'
    return response

def generateLinkformat(uri):
    # get related timemaps
    query_tm = RELATED_COMPLEX_WORKS % {'uri': uri}
    tm_str = sparqlQuery(query_tm, 'http://abel:8890/sparql')
    tm_obj = json.loads(tm_str)
    # get related original timegate
    query_ot = URI_G_TEMPLATE % {'uri': uri}
    ot_str = sparqlQuery(query_ot, 'http://abel:8890/sparql')
    ot_obj = json.loads(ot_str)
    # get related mementos
    query_m = RELATED_MEMENTOS % {'uri': uri}
    m_str = sparqlQuery(query_m, 'http://abel:8890/sparql')
    m_obj = json.loads(m_str)
    # get startdate, enddate and type of date
    timemap_list = []
    timemap_list.append(uri)
    for i in tm_obj['results']['bindings']:
        timemap_list.append(i['complex_work']['value'])
    timemap_info = {}
    for i in timemap_list:
        query_tminfo = TIMEMAPINFO % {'uri': i}
        print(i)
        tminfo_str = sparqlQuery(query_tminfo, 'http://abel:8890/sparql')
        tminfo_obj = json.loads(tminfo_str)
        timemap_info['http://localhost:5000/'+toLocalhostUri(i)] = (stringToHTTPDate(tminfo_obj['results']['bindings'][0]['startdate']['value']), \
                                                                   stringToHTTPDate(tminfo_obj['results']['bindings'][0]['enddate']['value']), \
                                                                   tminfo_obj['results']['bindings'][0]['typeofdate']['value'])                                                                
    
    response_body = ""
    for i in ot_obj['results']['bindings']:
       response_body += '<http://localhost:5000/'+toLocalhostUri(i['predecessor']['value'])+'>;rel="original timegate",\n'
    response_body += '<http://localhost:5000/'+toLocalhostUri(uri)+'?rel=timemap>;rel="self";type="application/link-format"' \
                     +';startdate="'+str(timemap_info['http://localhost:5000/'+toLocalhostUri(uri)][0])+'"' \
                     +';enddate="'+str(timemap_info['http://localhost:5000/'+toLocalhostUri(uri)][1])+'"' \
                     +';typeofdate="'+str(timemap_info['http://localhost:5000/'+toLocalhostUri(uri)][2])+'",\n'
    for i in tm_obj['results']['bindings']:
        response_body += '<http://localhost:5000/'+toLocalhostUri(i['complex_work']['value']) \
                         +'?rel=timemap>;rel="timemap";type="application/link-format"' \
                         +';startdate="'+str(timemap_info['http://localhost:5000/'+toLocalhostUri(i['complex_work']['value'])][0])+'"' \
                         +';enddate="'+str(timemap_info['http://localhost:5000/'+toLocalhostUri(i['complex_work']['value'])][1])+'"' \
                         +';typeofdate="'+str(timemap_info['http://localhost:5000/'+toLocalhostUri(i['complex_work']['value'])][2])+'",\n'
                          
    for i in m_obj['results']['bindings']:
       response_body += '<http://localhost:5000/'+toLocalhostUri(i['memento']['value'])+'>;rel="memento";datetime="'+i['date']['value']+'",\n'
    return response_body

def isComplexWork(uri):
    """check whether the uri represents an instance of type cdm:complex_work"""
    query = COMPLEX_WORK_TEMPLATE % {'uri': uri}
    #LOGGER.debug('COMPLEX_WORK_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    return json_obj['boolean'] == True and True or False


def determineDatetimeProperty(uri):
    """determine the cdm property used for datetime negotiation"""
    query = DATETIME_PROPERTY_TEMPLATE % {'uri': uri}
    #LOGGER.debug('DATETIME_PROPERTY_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    datetime_property = json_obj['results']['bindings'][0]['prop']['value']
    LOGGER.debug("Datetime negotiation property: %s" % datetime_property)
    return datetime_property


def determineLocation(uri, accept_datetime):
    """determine the location information for next redirect"""
    query = LOCATION_TEMPLATE % {
        'uri': uri, 'accept_datetime': accept_datetime}
    #LOGGER.debug('LOCATION_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    location = None
    try:
        location = json_obj['results']['bindings'][0]['successor']['value']
    except:
        LOGGER.debug(
            'determineLocation: Could not determine redirect location...')
    LOGGER.debug("Location: %s" % location)
    return location

def toCelexUri(uri):
    return uri.replace('memento', 'http://publications.europa.eu/resource/celex')


def toLocalhostUri(uri):
    return uri.replace('http://publications.europa.eu/resource/celex', 'memento')

def toLocalhostDataUri(uri, fext):
    return uri.replace('http://publications.europa.eu/resource/celex', 'data')+fext

def parseDate(text):
    """"parses a HTTP-date and returns a datetime object"""
    return datetime.datetime(*eut.parsedate(text)[:6])

def stringToHTTPDate(text):
    """converts an xsd:date into an HTTP-date string"""
    return datetime.datetime.strptime(text.replace('+02:00',''),'%Y-%m-%d').strftime('%a, %d %b %Y %H:%M:%S %Z')+(' GMT')

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    # set logging format
    logFormatter = logging.Formatter(
        "%(asctime)s [%(levelname)-5.5s]  %(message)s")
    # create LOGGER
    global LOGGER
    LOGGER = logging.getLogger()
    # set up file logging
    fileHandler = logging.handlers.RotatingFileHandler(
        "logging.log", maxBytes=1000000000, backupCount=2)
    fileHandler.setFormatter(logFormatter)
    LOGGER.addHandler(fileHandler)
    # set up console logging
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    LOGGER.addHandler(consoleHandler)
    app.run(debug=True)
    # run on public ip
    # app.run('0.0.0.0')
