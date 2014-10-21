from flask import Flask
from flask import request
import requests
import json
import logging
import logging.handlers

# suppress logging messages from requests lib
requests_log = logging.getLogger("requests")
requests_log.setLevel(logging.ERROR)


ROOT_TEMPLATE = (
    'PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> '
    'select distinct ?predecessor where { '
    '?predecessor cdm:complex_work_has_member_work* <%(uri)s>. '
    '?predecessor ?p ?o. '
    'filter not exists{?anotherWork cdm:complex_work_has_member_work ?predecessor.}} '
    )     
    
def sparqlQuery(query, base_url, format="application/json"):
    payload={
        "default-graph-uri": "",
        "query": query,
        "debug": "on",
        "timeout": "",
        "format": format,
    }
    resp = requests.get(base_url, params=payload)
    return resp.text

app = Flask(__name__)
    
@app.route('/memento/<id>')
def processRequest(id=None):
    uri = "http://publications.europa.eu/resource/celex/" + id
    query = ROOT_TEMPLATE % {'uri' : uri}
    json_str = sparqlQuery(query, 'http://abel:8890/sparql')
    json_obj = json.loads(json_str)
    root_id = json_obj['results']['bindings'][0]['predecessor']['value']
    if (root_id == uri):
        original_resource_callback()
    else:
        timegate_callback()
    return root_id
    

def original_resource_callback():
    LOGGER.debug('Executing original_resource_callback...')
    accept_datetime = None
    try:
        accept_datetime = request.headers['Accept-Datetime']
        LOGGER.debug('Accept-Datetime: %s' % accept_datetime)
    except KeyError as e:
        LOGGER.debug('No Accept-Datetime parameter provided in request. Defaulting to now()')
       
    pass
    
    
def timegate_callback():
    LOGGER.debug('Executing timegate_callback...')
    # to be implemented
    pass
    
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
