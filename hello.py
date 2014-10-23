from flask import Flask
from flask import request
from flask import redirect
from flask import make_response
import flask
import time
import requests
import json
import logging
import logging.handlers

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

# return memento datetime of given resource (corresponds to cdm:work_date_document)
MEMENTO_DATETIME_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select ?date ' 
    'where '
    '{<%(uri)s> cdm:work_date_document ?date.}'
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
def processRequest(id=None):
    """process request"""
    response = None
    uri = "http://publications.europa.eu/resource/celex/" + id
    # return rdf representation not a complex work
    if not(isComplexWork(uri)):
        response = mementoCallback(uri)
        return response
    query = URI_G_TEMPLATE % {'uri': uri}
    LOGGER.debug('URI_G_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    global uri_g
    uri_g = json_obj['results']['bindings'][0]['predecessor']['value']
    LOGGER.debug("URI-G: %s" % uri_g)
    if uri_g == uri:
        response = originalResourceCallback(uri)
    else:
        response = timegateCallback(uri)
    return response


def originalResourceCallback(uri_g):
    """processing logic when requesting an original resource"""
    LOGGER.debug('Executing originalResourceCallback...')
    accept_datetime = None
    location = None
    # redirect to intermediate resource
    if 'Accept-Datetime' in request.headers:
        accept_datetime = request.headers['Accept-Datetime']
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
            if location == None: break
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
    accept_datetime = ('Accept-Datetime' in request.headers) and request.headers[
        'Accept-Datetime'] or time.strftime("%Y-%m-%dT%XZ")
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
    describe_query = DESCRIBE_TEMPLATE  % {'uri': uri}
    #LOGGER.debug('DESCRIBE_TEMPLATE: %s' % describe_query )
    describe = sparqlQuery(describe_query, 'http://abel:8890/sparql', format='text/html')
    memento_datemtime_query = MEMENTO_DATETIME_TEMPLATE  % {'uri': uri}
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
    response = make_response(describe, 200)
    response.headers['Memento-Datetime'] = memento_datetime
    response.headers['Link'] = '<%(localhost_uri_g)s>; rel="original timegate", ' \
        '<%(localhost_uri_t)s>; rel="timemap"' % {
            'localhost_uri_g': localhost_uri_g, 'localhost_uri_t': localhost_uri_t}
    return response
    
    
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
        LOGGER.debug('determineLocation: Could not determine redirect location...')
    LOGGER.debug("Location: %s" % location)
    return location

def toCelexUri(uri):
    return uri.replace('memento', 'http://publications.europa.eu/resource/celex')

def toLocalhostUri(uri):
    return uri.replace('http://publications.europa.eu/resource/celex', 'memento')


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
