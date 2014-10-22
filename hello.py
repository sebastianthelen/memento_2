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

# global variable stores uri of original resource uthread safe???!?!
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

# query determines in which dimension an evolutive work should
# perform datetime negotiation in
DATETIME_PROPERTY_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select distinct ?prop where {<%(uri)s> cdm:datetime_negotiation ?prop.}'
)

# query computes the location information of the next redirect based on
# in the current uri and the accept-datetime parameter
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

DESCRIBE_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'DESCRIBE <%(uri)s> '
)

# query tests whether a given work is an instance of cdm:complex_work
COMPLEX_WORK_TEMPLATE = (
    'define input:inference "cdm_rule_set" '
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'ASK { <%(uri)s> a <http://publications.europa.eu/ontology/cdm#complex_work>.}'
)

# perform sparql query and return result as json format


def sparqlQuery(query, base_url, format="application/json"):
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
    response = None
    uri = "http://publications.europa.eu/resource/celex/" + id
    
    if not(isComplexWork(uri)):
        response = mementoCallback(uri)
        return response
        
    query = URI_G_TEMPLATE % {'uri': uri}
    #LOGGER.debug('URI_G_TEMPLATE: %s' % query )
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
    LOGGER.debug('Executing originalResourceCallback...')
    accept_datetime = None
    location = None
    if 'Accept-Datetime' in request.headers:
        # redirect to intermediate resource (timegate)
        accept_datetime = request.headers['Accept-Datetime']
        LOGGER.debug('Accept-Datetime: %s' % accept_datetime)
        # determine negotiation dimension
        datetime_property = determineDatetimeProperty(uri_g)
        # compute redirect
        location = determineLocation(uri_g, accept_datetime)
    else:
        # redirect to most recent representation
        #location = computeMostRecentRepresentation(uri_g)

        LOGGER.debug('Executing computeMostRecentRepresentation...')
        now = time.strftime("%Y-%m-%dT%XZ")
        location = uri_g
        while isComplexWork(location):
            location = determineLocation(location, now)
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
    LOGGER.debug('Executing timegateCallback...')
    # default to now if no accept-datetime is provided
    accept_datetime = ('Accept-Datetime' in request.headers) and request.headers[
        'Accept-Datetime'] or time.strftime("%Y-%m-%dT%XZ")
    # dimension of datetime negotiation
    datetime_property = determineDatetimeProperty(uri)
    # compute redirect
    location = determineLocation(uri, accept_datetime)
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

def mementoCallback(uri)
    
# checks whether the uri represents an instance of type cdm:complex_work
def isComplexWork(uri):
    query = COMPLEX_WORK_TEMPLATE % {'uri': uri}
    #LOGGER.debug('COMPLEX_WORK_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    return json_obj['boolean'] == True and True or False


# determines the cdm property used for datetime negotiation
def determineDatetimeProperty(uri):
    query = DATETIME_PROPERTY_TEMPLATE % {'uri': uri}
    #LOGGER.debug('DATETIME_PROPERTY_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    datetime_property = json_obj['results']['bindings'][0]['prop']['value']
    LOGGER.debug("Datetime negotiation property: %s" % datetime_property)
    return datetime_property

# determines the location information for the next redirect


def determineLocation(uri, accept_datetime):
    query = LOCATION_TEMPLATE % {
        'uri': uri, 'accept_datetime': accept_datetime}
    #LOGGER.debug('LOCATION_TEMPLATE: %s' % query )
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    location = json_obj['results']['bindings'][0]['successor']['value']
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
