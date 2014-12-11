# Authors: Sebastian Thelen, Patrick Gratz
# Description: The following code represents a prototypical implementation of the Memento framework (RFC 7089). For further information concerning Memento we refer to http://www.mementoweb.org/.
# Prerequisites: Python 3.x, Flask microframework for Python
# (http://flask.pocoo.org/), Virtuoso 7 or a triple store with an
# equivalent SPARQL endpoint

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
import re

# suppress logging messages from requests lib
requests_log = logging.getLogger("requests")
requests_log.setLevel(logging.ERROR)

# global variable stores uri of original resource uthread safe???!?!
uri_g = None
local_host = 'http://localhost:5000'
sparql_endpoint = 'http://abel:8890/sparql'
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
EVOLUTIVE_WORK_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'SELECT ?p where { <%(uri)s> a <http://publications.europa.eu/ontology/cdm#evolutive_work>; ?p ?o.}'
)

# return memento datetime of given resource (corresponds to
# cdm:work_date_document)
MEMENTO_DATETIME_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select ?date '
    'where '
    '{<%(uri)s> ?p ?date;'
    '^cdm:complex_work_has_member_work ?tg.'
    '?tg cdm:datetime_negotiation ?p.}'
)

# return related complex works
RELATED_EVOLUTIVE_WORKS = (
    'prefix cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select distinct ?evolutive_work where {'
    '<%(uri)s> cdm:complex_work_has_member_work|^cdm:complex_work_has_member_work ?evolutive_work.'
    '?evolutive_work a cdm:evolutive_work. }'
)
# return related mementos together with their memento-datetime
RELATED_MEMENTOS = (
    'prefix cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select distinct ?memento ?date where {'
    '<%(uri)s> cdm:complex_work_has_member_work ?memento.'
    '?memento cdm:work_date_creation ?date.'
    'filter not exists { ?memento cdm:complex_work_has_member_work ?member.} }'
)

# return timeamp related information (startdate, enddate and type of date)
TIMEMAPINFO = (
    'prefix cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select (min(?o) as ?startdate) (max(?o) as ?enddate) (?p as ?typeofdate) where {'
    '<%(uri)s> cdm:datetime_negotiation ?p;  '
    'cdm:complex_work_has_member_work ?member. '
    '?member ?p ?o.}'
)


def sparqlQuery(query, format="application/json"):
    """perform sparql query and return the corresponding bindings"""
    payload = {
        "default-graph-uri": "",
        "query": query,
        "debug": "on",
        "timeout": "",
        "format": format
    }
    resp = requests.get(sparql_endpoint, params=payload)
    if format == "application/json":
        json_results = json.loads(resp.text)
        return json_results['results']['bindings']
    return resp.text


@app.route('/memento/<id>')
def processMementoRequest(id=None):
    """process memento service request (non-information resources)"""
    response = None
    uri = "http://publications.europa.eu/resource/celex/" + id
    # return memento (target resource is not a complex work)
    if not(isEvolutiveWork(uri)):
        response = mementoCallback(uri)
        return response
    query = URI_G_TEMPLATE % {'uri': uri}
    sparql_results = sparqlQuery(query)
    global uri_g
    uri_g = sparql_results[0]['predecessor']['value']
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
def processDataRequest(id=None):
    """process data representation request (information resources)"""
    LOGGER.debug('Processing data request ...')

    response = None
    uri = "http://publications.europa.eu/resource/celex/" + id
    if id.endswith('.txt'):
        # return application/link-format
        response = dataRepresentationCallback(uri.replace('.txt', ''), True)
    else:
        # return application/rdf+xml
        response = dataRepresentationCallback(uri.replace('.xml', ''), False)
    return response


def originalTimegateCallback(uri_g):
    """processing logic when requesting an original resource"""
    LOGGER.debug('Executing originalResourceCallback...')
    accept_datetime = None
    location = None
    # redirect to intermediate timegate resource
    if 'Accept-Datetime' in request.headers:
        accept_datetime = parseHTTPDate(request.headers['Accept-Datetime'])
        LOGGER.debug('Accept-Datetime: %s' % accept_datetime)
        # determine negotiation dimension
        datetime_property = determineDatetimeProperty(uri_g)
        # compute location information of redirect
        location = determineLocation(uri_g, accept_datetime)
    # redirect to most recent representation
    else:
        # current timestamp
        now = time.strftime("%Y-%m-%dT%XZ")
        location = uri_g
        # cascading selection of most recent representation
        while isEvolutiveWork(location):
            location = determineLocation(location, now)
            if location == None:
                break
    # actually it is not Memento compliant to return an HTTP 406 here.
    # See section 4.5.3 of the specification
    if location == None:
        return make_response("Bad Request. Check your query parameters", 406)
    # link headers
    localhost_uri_g = toLocalhostUri(uri_g)
    localhost_uri_t = toLocalhostUri(uri_g + '?rel=timemap')
    LOGGER.debug(localhost_uri_g)
    LOGGER.debug(localhost_uri_t)
    # return redirection object
    redirect_obj = redirect(toLocalhostUri(location), code=302)
    redirect_obj.headers['Link'] = '<%(localhost_uri_g)s>; rel="original timegate", ' \
        '<%(localhost_uri_t)s>; rel="timemap"' % {
            'localhost_uri_g': localhost_uri_g, 'localhost_uri_t': localhost_uri_t}
    redirect_obj.headers['Vary'] = 'accept-datetime'
    return redirect_obj


def timegateCallback(uri):
    """processing logic when requesting an intermediate timegate"""
    LOGGER.debug('Executing timegateCallback...')
    # default to now if no accept-datetime is provided
    accept_datetime = ('Accept-Datetime' in request.headers) and parseHTTPDate(request.headers[
        'Accept-Datetime']) or time.strftime("%Y-%m-%dT%XZ")
    # dimension of datetime negotiation
    datetime_property = determineDatetimeProperty(uri)
    # compute redirect
    location = determineLocation(uri, accept_datetime)
    if location == None:
        return make_response("Bad Request. Check your query parameters", 406)
    # link headers
    localhost_uri_g = toLocalhostUri(uri_g)
    localhost_uri_tg = toLocalhostUri(uri_g + '?rel=timemap')
    localhost_uri = toLocalhostUri(uri)
    localhost_uri_t = localhost_uri + '?rel=timemap'
    LOGGER.debug(localhost_uri_g)
    LOGGER.debug(localhost_uri_tg)
    # return redirection object
    redirect_obj = redirect(toLocalRedirectUri(location), code=302)
    redirect_obj.headers['Link'] = '<%(localhost_uri_g)s>; rel="original timegate", ' \
        '<%(localhost_uri_tg)s>; rel="timemap", <%(localhost_uri)s>; rel="timegate", ' \
        '<%(localhost_uri_t)s>; rel="timemap" ' % {
            'localhost_uri_g': localhost_uri_g, 'localhost_uri_tg': localhost_uri_tg,
            'localhost_uri': localhost_uri, 'localhost_uri_t': localhost_uri_t}
    mementoDatetimeResponseObj = getMementoDatetime(uri)
    # memento datetime could not be retrieved
    # --> return 404 error object
    if mementoDatetimeResponseObj.status_code == 404:
        return mementoDatetimeResponseObj
    print(type(mementoDatetimeResponseObj))
    redirect_obj.headers[
        'Memento-Datetime'] = stringToHTTPDate(mementoDatetimeResponseObj.data.
                                               decode(encoding='UTF-8'))
    return redirect_obj


def mementoCallback(uri):
    """processing logic when requesting a memento"""
    LOGGER.debug('Executing mementoCallback...')
    mementoDatetimeResponseObj = getMementoDatetime(uri)
    # memento datetime could not be retrieved
    # --> return 404 error object
    if mementoDatetimeResponseObj.status_code == 404:
        return mementoDatetimeResponseObj
    localhost_uri_g = toLocalhostUri(uri_g)
    localhost_uri_t = toLocalhostUri(uri_g + '?rel=timemap')
    response = redirect(toLocalRedirectDataUri(uri, '.xml'), code=303)
    response.headers[
        'Memento-Datetime'] = stringToHTTPDate(mementoDatetimeResponseObj.data.
                                               decode(encoding='UTF-8'))
    response.headers['Link'] = '<%(localhost_uri_g)s>; rel="original timegate", ' \
        '<%(localhost_uri_t)s>; rel="timemap"' % {
            'localhost_uri_g': localhost_uri_g, 'localhost_uri_t': localhost_uri_t}
    return response


def getMementoDatetime(uri):
    """return response containing memento-datetime for a given resource"""
    memento_datemtime_query = MEMENTO_DATETIME_TEMPLATE % {'uri': uri}
    #LOGGER.debug('MEMENTO_DATETIME_TEMPLATE: %s' % memento_datemtime_query )
    sparql_results = sparqlQuery(memento_datemtime_query)
    response = None
    try:
        memento_datetime = sparql_results[0]['date']['value']
        response = make_response(memento_datetime, 200)
    except:
        response = make_response(
            "Not found. Could not retrieve resource metadata", 404)
    return response


def timemapCallback(uri):
    """processing logic when requesting a timemap"""
    LOGGER.debug('Executing timemapCallback...')
    localhost_uri_g = toLocalhostUri(uri_g)
    redirect_obj = None
    if(request.headers['Accept'] == 'application/link-format'):
        # redirect to link-format representation
        redirect_obj = redirect(toLocalRedirectDataUri(uri, '.txt'), code=303)
    else:
        # redirect to rdf/xml representation
        redirect_obj = redirect(toLocalRedirectDataUri(uri, '.xml'), code=303)
    redirect_obj.headers['Link'] = '<%(localhost_uri_g)s>; rel="original timegate"' % {
        'localhost_uri_g': localhost_uri_g}
    return redirect_obj


def dataRepresentationCallback(uri, linkformat):
    """processing logic when requesting a data representation (information resource)"""
    LOGGER.debug('Executing dataRepresentationCallback...')
    if linkformat:
        tm = generateLinkformatTimemap(uri)
        response = make_response(tm, 200)
        response.headers[
            'Content-Type'] = 'application/link-format; charset=utf-8'
    else:
        describe_query = DESCRIBE_TEMPLATE % {'uri': uri}
        sparql_results = sparqlQuery(
            describe_query, format='application/rdf+xml')
        response = make_response(sparql_results, 200)
        response.headers['Content-Type'] = 'application/rdf+xml; charset=utf-8'
    return response


def generateLinkformatTimemap(uri):
    """generate timemap in link-value format"""
    # get related timemaps
    query_tm = RELATED_EVOLUTIVE_WORKS % {'uri': uri}
    tm_results = sparqlQuery(query_tm)
    # get related original timegate
    query_ot = URI_G_TEMPLATE % {'uri': uri}
    ot_results = sparqlQuery(query_ot)
    # get related mementos
    query_m = RELATED_MEMENTOS % {'uri': uri}
    m_results = sparqlQuery(query_m)
    # get startdate, enddate and type of date
    timemap_list = [uri]
    timemap_list.append(uri)
    for i in tm_results:
        timemap_list.append(i['evolutive_work']['value'])
    timemap_info = {}
    for i in timemap_list:
        query_tminfo = TIMEMAPINFO % {'uri': i}
        tminfo_results = sparqlQuery(query_tminfo)
        timemap_info[toLocalhostUri(i)] = (stringToHTTPDate(tminfo_results[0]['startdate']['value']),
                                           stringToHTTPDate(
                                               tminfo_results[0]['enddate']['value']),
                                           tminfo_results[0]['typeofdate']['value'])
    # add link to the original timegate
    response_body = ''.join(
        ['<' + toLocalhostUri(i['predecessor']['value']) + '>;rel="original timegate"\n' for i in ot_results])
    # add link for each memento
    response_body += ''.join(['<' + toLocalhostUri(i['memento']['value']) +
                              '>;rel="memento";datetime="' + stringToHTTPDate(i['date']['value']) + '"\n' for i in m_results])
    # add link for timemaps
    response_body += ''.join(['<' + toLocalhostUri(i['evolutive_work']['value'])
                              +
                              '?rel=timemap>;rel="timemap";type="application/link-format"'
                              + ';from="' +
                              str(timemap_info[
                                  toLocalhostUri(i['evolutive_work']['value'])][0]) + '"'
                              + ';until="' +
                              str(timemap_info[
                                  toLocalhostUri(i['evolutive_work']['value'])][1]) + '"'
                              + ';dtype="' + str(timemap_info[toLocalhostUri(i['evolutive_work']['value'])][2]) + '"\n' for i in tm_results])
    # add link to self
    response_body += '<' + toLocalhostUri(uri) + '?rel=timemap>;rel="self";type="application/link-format"' \
                     + ';from="' + str(timemap_info[toLocalhostUri(uri)][0]) + '"' \
                     + ';until="' + str(timemap_info[toLocalhostUri(uri)][1]) + '"' \
                     + ';dtype="' + \
        str(timemap_info[toLocalhostUri(uri)][2]) + '"'
    return response_body


def isEvolutiveWork(uri):
    """check whether the uri represents an instance of type cdm:complex_work"""
    query = EVOLUTIVE_WORK_TEMPLATE % {'uri': uri}
    #LOGGER.debug('EVOLUTIVE_WORK_TEMPLATE: %s' % query )
    sparql_results = sparqlQuery(query)
    LOGGER.debug(sparql_results == [])
    return (sparql_results != [])


def determineDatetimeProperty(uri):
    """determine the cdm property used for datetime negotiation"""
    query = DATETIME_PROPERTY_TEMPLATE % {'uri': uri}
    LOGGER.debug('DATETIME_PROPERTY_TEMPLATE: %s' % query)
    sparql_results = sparqlQuery(query)
    datetime_property = sparql_results[0]['prop']['value']
    LOGGER.debug("Datetime negotiation property: %s" % datetime_property)
    return datetime_property


def determineLocation(uri, accept_datetime):
    """determine the location information for next redirect"""
    query = LOCATION_TEMPLATE % {
        'uri': uri, 'accept_datetime': accept_datetime}
    #LOGGER.debug('LOCATION_TEMPLATE: %s' % query )
    sparql_results = sparqlQuery(query)
    location = None
    try:
        location = sparql_results[0]['successor']['value']
    except:
        LOGGER.debug(
            'determineLocation: Could not determine redirect location...')
    LOGGER.debug("Location: %s" % location)
    return location


def toCelexUri(uri):
    """transform a local memento uri into celex uri"""
    return uri.replace('memento', 'http://publications.europa.eu/resource/celex')


def toLocalRedirectUri(uri):
    """transform a celex uri into a relative, local,  memento uri"""
    return uri.replace('http://publications.europa.eu/resource/celex', 'memento')


def toLocalRedirectDataUri(uri, fext):
    """transform a celex uri into a relative, local, data uri"""
    return uri.replace('http://publications.europa.eu/resource/celex', 'data') + fext


def toLocalhostUri(uri):
    """transform a celex uri into an absolute, local, memento uri"""
    return uri.replace('http://publications.europa.eu/resource/celex', '%(localhost)s/memento' % {'localhost': local_host})


def toLocalhostDataUri(uri, fext):
    """transform a celex uri into an absolute, local, data uri"""
    return uri.replace('http://publications.europa.eu/resource/celex', '%(localhost)s/data' % {'localhost': local_host}) + fext


def parseHTTPDate(text):
    """"parse a HTTP-date and returns a datetime object"""
    return datetime.datetime(*eut.parsedate(text)[:6])


def stringToHTTPDate(text):
    """convert a xsd:date into an HTTP-date string"""
    return datetime.datetime.strptime(text.rsplit('+', 1)[0], '%Y-%m-%d').strftime('%a, %d %b %Y %H:%M:%S %Z') + (' GMT')

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
