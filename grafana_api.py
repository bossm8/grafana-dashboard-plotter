"""
Client implementation for the Grafana API

@Licence: MIT
@Author: Boss Marco <bossm8@hotmail.com>
"""
from re import compile
from time import time_ns
from urllib3 import disable_warnings
from requests import request, Response
from urllib3.exceptions import InsecureRequestWarning
from logging import getLogger

_logger = getLogger('default')


class DataSourceError(Exception):
    pass


class ApiError(Exception):
    pass


class GrafanaClient:
    """
    The Grafana API client
    """

    def __init__(self,
                 base_url,
                 api_key,
                 from_ms: int = None,
                 to_ms: int = None,
                 node_exporter_job_name: str = 'node',
                 tls_verify: bool = True):
        """
        Create a grafana client.
        From and To are static, means all queries and renders are done in the time period in between those two.

        :param base_url: The base url of the grafana instance including scheme and port
        :param api_key: An admin level api key for the instance, since requesting datasources is needed
        :param from_ms: Start timestamp in ms which is used when querying datasources or the render api
                        (default: now-1h)
        :param to_ms: End timestamp in ms which is used when querying datasources or the render api
                      (default: now)
        :param node_exporter_job_name: The name of the job (scrape_config) for the prometheus node exporter
        :param tls_verify: If the instances certificate should be verified
        """

        self.base_url = base_url
        self.verify = tls_verify
        if not self.verify:
            _logger.info('Skipping certificate verification')
            disable_warnings(InsecureRequestWarning)
        _current_time_ms = int(time_ns() // 1_000_000)
        self.from_ms = from_ms if from_ms is not None else _current_time_ms - (3600 * 1000)
        self.to_ms = to_ms if to_ms is not None else _current_time_ms
        self.node_exporter_job_name = node_exporter_job_name
        self.default_params = {
            'theme': 'light',
            'orgId': '1',
            'from': self.from_ms,
            'to': self.to_ms
        }
        self.default_headers = {
            'Authorization': 'Bearer ' + api_key,
            'Accept': 'application/json'
        }
        self.datasources = self.__do_request('GET', '/api/datasources').json()

    def get_datasource_json(self,
                            datasource) -> dict:
        """
        Get the json of a specific datasource

        :param datasource: Either the name or the json {type: <>, uid: <>} of a datasource
        :return: The datasources json representation
        """

        if type(datasource) is dict:
            for ds in self.datasources:
                if ds['uid'] == datasource['uid']:
                    return ds
        else:
            for ds in self.datasources:
                if ds['name'] == datasource:
                    return ds

        raise DataSourceError(f'Datasource `{datasource}` is not available')

    def __do_request(self,
                     method: str,
                     uri: str,
                     params: dict = None) -> Response:
        """
        Do a request to the api.
        This method will abort the program execution if there was an error.

        :param method: Http method of the request
        :param uri: The path of the resource to request
        :param params: The query parameters to add to the request
        :return: The resulting response
        """

        if params is None:
            params = {}

        resp = request(method=method,
                       url=self.base_url + uri,
                       headers=self.default_headers,
                       params=params,
                       verify=self.verify)

        if not resp.ok:
            raise ApiError(f'Request for `{resp.url}` failed with {resp.status_code} {resp.reason}')

        _logger.debug(f'Successfully requested `{resp.url}`')
        return resp

    def get_dashboard_json(self,
                           uid: str):
        """
        Get the json representation of a grafana dashboard
        :param uid: The uid of the dashboard (can be found in the url of thr UI)
        :return: The json representation of the dashboard
        """

        return self.__do_request('GET',
                                 uri='/api/dashboards/uid/' + uid).json()

    def d_solo_render(self,
                      dashboard_uid: str,
                      dashboard_slug: str,
                      params: dict = None) -> Response:
        """
        Query the render endpoint on /d-solo/ for a single panel

        :param dashboard_uid: The uid of the dashboard
        :param dashboard_slug: The slug of the dashboard
        :param params: The query parameters to pass to the render endpoint.
                       Unfortunately there is currently no available documentation of the render api but parameters
                       may include var-<VAR_NAME>=<VAR_VALUE>
                       Default added parameters are theme=light, ordId=1
        :return: The resulting http response
        """

        return self.__do_request('GET',
                                 uri=f'/render/d-solo/{dashboard_uid}/{dashboard_slug}',
                                 params=self.default_params | params)

    def __datasource_proxy(self,
                           uri: str,
                           params: dict = None) -> Response:
        """
        Do a proxy request to the datasources in grafana.

        :param uri: the full query uri of the datasource (without the grafana part)
        :param params: Query parameters to pass to the datasource
        :return: The resulting http response
        """

        return self.__do_request('GET',
                                 f'/api/datasources/proxy/{uri}',
                                 params)

    def __prom_label_values(self,
                            metric: str,
                            label: str,
                            datasource_id: int) -> list:
        """
        Get prometheus label values.

        :param metric: The metric query to get label values from (can be empty)
        :param label: The label name to get values from
        :param datasource_id: The prometheus datasource which to query
        :return: The resulted label values as an array
        """

        if metric == '':
            # There is no metric so just query this must be a directly reachable label
            result = self.__datasource_proxy(f'{datasource_id}/api/v1/label/{label}/values')
            result = result.json()['data']
        else:
            # Send the metric query to prometheus
            if 'node' in metric:
                # if the metric query begins with node, try to replace $job with the configured values
                metric = metric.replace('$job', self.node_exporter_job_name)
            params = {
                'match[]': metric,
                # Prometheus uses seconds
                'start': int(self.from_ms / 1000),
                'end': int(self.to_ms / 1000),
            }
            result = self.__datasource_proxy(f'{datasource_id}/api/v1/series', params).json()
            # Extract the required values from the json and filter out unwanted multi value options
            result = map(lambda m: m.get(label, ''), result['data'])
            result = filter(lambda v: v != '$__all' and v != '', result)
            result = list(set(result))

        return result

    def query_prometheus(self,
                         query: dict,
                         datasource: dict):
        """
        Query the prometheus datasource

        :param query: The json query retrieved from a dashboard
        :param datasource: Which prometheus datasource to use
        :return: The processed result (e.g. for label_values a list)
        """

        label_values_regex = compile(
            '^label_values\((?:(.+),\s*)?([a-zA-Z_][a-zA-Z0-9_]*)\)\s*$'
        )
        if label_values_regex.match(query['query']):
            result = label_values_regex.findall(query['query'])
            metric, label = result[0]
            return self.__prom_label_values(metric, label, datasource['id'])

    def execute_query(self,
                      query: dict,
                      datasource):
        """
        Execute a query on a datasource via grafana
        :param query: The json query extracted from the dashboard e.g. dashboard.templating.list[].query
        :param datasource: The name or the json of the datasource to execute the query against, also in the dashboard,
                           e.g. dashboard.templating.list[].datasource (json: {type: <>, uid: <>})
        :return: The processed result data of the query (e.g. a list for label_values)
        """

        ds = self.get_datasource_json(datasource)
        if ds['type'] == "prometheus":
            return self.query_prometheus(query, ds)
        elif ds['type'] == "loki":
            raise DataSourceError('Loki queries not implemented yet')
        else:
            raise DataSourceError(f'Unsupported datasource type `{ds["type"]}`')
