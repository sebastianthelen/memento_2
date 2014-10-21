from flask import Flask
from flask import request
from flask import redirect
import time
import requests
import json
import logging
import logging.handlers

# suppress logging messages from requests lib
requests_log = logging.getLogger("requests")
requests_log.setLevel(logging.ERROR)

# thread safe???!?!
uri_g = None

app = Flask(__name__)

# query computes the original resource (URI-G) in a hierarchy
URI_G_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select distinct ?predecessor where { '
    '?predecessor cdm:complex_work_has_member_work* <%(uri)s>. '
    '?predecessor ?p ?o. '
    'filter not exists{?anotherWork cdm:complex_work_has_member_work ?predecessor.}} '
)

DATETIME_PROPERTY_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select distinct ?prop where {<%(uri)s> cdm:datetime_negotiation ?prop.}'
)  
    
# time format 2013-07-02T12:00:00Z

LOCATION_TEMPLATE = (
    "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
    "SELECT distinct ?successor (bif:datediff( 'minute', xsd:dateTime(str(?date)) ,'%(accept_datetime)s'^^xsd:dateTime) as ?diff_date) "
    "WHERE "
    "{ "
    "<%(uri)s> cdm:datetime_negotiation ?datetime_property; "
    "cdm:complex_work_has_member_work+ ?individual_work; "
    "cdm:complex_work_has_member_work ?successor. "
    "?successor cdm:complex_work_has_member_work+ ?individual_work. "
    "?individual_work ?datetime_property ?date. "
    "FILTER (xsd:dateTime(?date) <= '%(accept_datetime)s'^^xsd:dateTime) "
    "} "
    "ORDER BY ASC(?diff_date) "
    "LIMIT 1 "
)

COMPLEX_WORK_TEMPLATE = (
    'define input:inference "cdm_rule_set" '
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'ASK { <%(uri)s> a <http://publications.europa.eu/ontology/cdm#complex_work>.}'
)
    
def sparqlQuery(query, base_url, format="application/json"):
    payload={
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
    uri = "http://publications.europa.eu/resource/celex/" + id
    query = URI_G_TEMPLATE % {'uri' : uri}
    LOGGER.debug('URI_G_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    global uri_g
    uri_g = json_obj['results']['bindings'][0]['predecessor']['value']
    LOGGER.debug("URI-G: %s" % uri_g)
    response = None
    if uri_g == uri:
        response = originalResourceCallback(uri)
    else:
        response = timegateCallback(uri)
    return response
    

def originalResourceCallback(uri_g):
    LOGGER.debug('Executing originalResourceCallback...')
    accept_datetime = None
    response = None
    if 'Accept-Datetime' in request.headers:
        # redirect to intermediate resource (timegate)
        accept_datetime = request.headers['Accept-Datetime']
        LOGGER.debug('Accept-Datetime: %s' % accept_datetime)
        response = computeRedirect(uri_g, accept_datetime)    
    else:
        # redirect to most recent representation
        response = computeMostRecentRepresentation(uri_g)
    return response
    
def computeRedirect(uri, accept_datetime):
    # determine negotiation dimension
    datetime_property = determineDatetimeProperty(uri)
    # compute redirect location
    location = determineLocation(uri, accept_datetime)
    location = 'memento/%s' % location.replace('http://publications.europa.eu/resource/celex/', '')
    # 4. return redirection object 
    redirect_obj = redirect(location, code=302)
    redirect_obj.headers['Link']='<%(uri_g)s>; rel="original timegate"' % {'uri_g' : uri_g}
    return redirect_obj
    
def computeMostRecentRepresentation(uri):
    now = time.strftime("%Y-%m-%dT%XZ")
    current = uri
    while (isComplexWork(current)):
        location = determineLocation(current, now)
        current = location
    redirect_obj = redirect(current, code=302)
    redirect_obj.headers['Link']='<%(uri_g)s>; rel="original timegate"' % {'uri_g' : uri_g}
    return redirect_obj 
    
def isComplexWork(uri):
    query = COMPLEX_WORK_TEMPLATE % {'uri' : uri}
    LOGGER.debug('COMPLEX_WORK_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    return json_obj['boolean']=="true" and True or False
        
def timegateCallback():
    # to be implemented
    LOGGER.debug('timegateCallback to be implemented...')
    return None
    
def determineDatetimeProperty(uri):
    query = DATETIME_PROPERTY_TEMPLATE % {'uri' : uri}
    LOGGER.debug('DATETIME_PROPERTY_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    datetime_property = json_obj['results']['bindings'][0]['prop']['value']
    LOGGER.debug("Datetime negotiation property: %s" % datetime_property)
    return datetime_property

def determineLocation(uri, accept_datetime):
    query = LOCATION_TEMPLATE % {'uri' : uri, 'accept_datetime' : accept_datetime}
    LOGGER.debug('LOCATION_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    location = json_obj['results']['bindings'][0]['successor']['value']
    #location = 'memento/%s' % location.replace('http://publications.europa.eu/resource/celex/', '')
    LOGGER.debug("Location: %s" % location)
    return location
    
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    
    # set logging format
    logFormatter = logging.Formatter(
        "%(asctime)s [%(levelname)-5.5s]  %(message)s")

    # create LOGGER
    global LOGGER
    LOGGER = logging.getLogger()

    # set up console logging
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    LOGGER.addHandler(consoleHandler)
    
    app.run(debug=True)
