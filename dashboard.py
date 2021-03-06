"""
Grafana Dashboard implementation to save its panels as png plots

@Licence: MIT
@Author: Boss Marco <bossm8@hotmail.com>
"""

import re
from grafana_api import GrafanaClient
from slugify import slugify
from pathlib import Path
from os import path as os_path
from re import compile
from logging import getLogger

_logger = getLogger('default')


class VariableError(Exception):
    pass


class Variable:
    """
    Dashboard Variable

    These are the variables and corresponding values which are shown in the grafana UI top left corner
    """

    def __init__(self,
                 name: str,
                 values: list):
        """
        :param name: The name of the variable
                     This must be the name which is used in queries, not the display name
                     The correct value can be found in dashboard->settings->variables.

        :param values: All possible values of the variable, make sure no multiple value
                       values are contained (such as $__all for example).
        """

        self.name = name
        self.values = values

    def __str__(self):
        return f'{self.name}: {self.values}'


class Dashboard:
    """
    Grafana Dashboard
    """

    # The panel of which plots are made from currently
    current_panel: dict

    def __init__(self,
                 grafana_client: GrafanaClient,
                 uid: str,
                 graph_width: int = 1200,
                 graph_height: int = 500,
                 variables: list = None,
                 ignore_regex: str = '',
                 render_collapsed: bool = False):
        """
        :param grafana_client: The client of which is connected to the grafana instance to create plots from
        :param uid: The uid of the dashboard
        :param graph_width: Width to render timeseries and graph panels
        :param graph_height: Height to render timeseries and graph panels
        :param variables: List of variables names which shall be used when querying for plots
                          (not display names, correct values can be found in dashboard->settings->variables).
                          Make sure not too many are passed since it creates many plots (intervals for example).
                          For variables which are not listed here, the default which is set in the grafana UI is used.
        :param ignore_regex: Regular expression matching values of the variable which shall be ignored.
        :param render_collapsed: If panels located inside collapsed rows should be rendered too
        """

        self.grafana_client = grafana_client
        self.uid = uid
        self.graph_width = graph_width
        self.graph_height = graph_height
        self.render_collapsed = render_collapsed

        dash_json = self.grafana_client.get_dashboard_json(self.uid)
        self.slug = dash_json['meta']['slug']
        self.json = dash_json['dashboard']

        _logger.debug(f'Dash: {self.uid} has slug `{self.slug}`')

        if variables is None:
            variables = []
        regex = compile("|".join(variables))

        _logger.debug(f'Dash: {self.uid} using variable regex `{regex.pattern}`')

        variables_json = self.json['templating']['list']
        variables_json = filter(lambda v: regex.fullmatch(v['name']),
                                variables_json)

        self.variables = []
        for var in variables_json:
            self.__resolve_variable(var,
                                    ignore_regex)

        _logger.debug(f'Dash: {self.uid} variables: {self.variables}')

    def __resolve_variable(self,
                           var: dict,
                           ignore: str = '') -> None:
        """
        Resolve the values for the selected variables via grafana

        :param var: The name of the variable to resolve
        :param ignore: Regular expression matching values of the variable which shall be ignored
        """

        v_type = var['type']

        if v_type == 'custom':
            # Custom variables have predefined and constant values, no need for special resolving
            values = map(lambda v: v['value'],
                         var['options'])
            values = filter(lambda o: o != '$__all',
                            values)
        elif v_type == 'interval':
            # Intervals should be used sparingly, but they are handled anyway
            # First the values are extracted from the array of interval objects,
            # then unwanted multi value options are removed
            values = map(lambda v: v['value'],
                         var['options'])
            values = filter(lambda o: o != '$__auto_interval_interval',
                            values)
        elif v_type == 'query':
            # Query types need to be resolved by querying the respective datasource,
            # fortunately this can be done via grafana's builtin proxy
            values = self.grafana_client.execute_query(
                var['query'],
                var['datasource']
            )
        else:
            # Abort if the variable is not known
            raise VariableError(f'Variable type `{v_type}` is currently not supported')

        if ignore != '':
            ignore = compile(ignore)
            values = filter(lambda o: not ignore.match(o),
                            values)

        self.variables.append(Variable(
            var['name'],
            list(values)
        ))

    def create_plots(self,
                     base_dir: str) -> None:
        """
        Save all panels of this dashboard as a png plot

        :param base_dir: The base directory to store the plots into.
        """

        _dir = os_path.join(base_dir, self.slug)
        Path(_dir).mkdir(exist_ok=True)

        for panel in self.json['panels']:
            if panel['type'] != 'row':
                self.create_panel_plot(_dir, panel)
            elif self.render_collapsed and panel['collapsed'] is True:
                for collapsed_panel in panel['panels']:
                    self.create_panel_plot(_dir, collapsed_panel)

    def create_panel_plot(self,
                          base_dir: str,
                          panel: dict) -> None:
        """
        Save one single panel as a png plot

        :param base_dir: The base directory to store the plot into.
        :param panel: The panel json retrieved from the original dashboard json.
        """

        self.current_panel = panel
        self.__rec_create_panel_plot(base_dir, {})

    def __rec_create_panel_plot(self,
                                dir: str,
                                params: dict,
                                var_index: int = 0) -> None:
        """
        Recursive algorithm to save a panels plots.
        Each panel will be checked for which variables are used in its query
        to assure no unwanted duplicates are created (each variable value combination is queried).

        :param dir: The base directory for the panel.
                    The actual plot will be placed in subdirectories named after the variable value it is created with.
        :param params: The params which are passed as query parameters to the grafana api.
                       Must be in the form of var-<VARIABLE_NAME>=<VARIABLE_VALUE>.
                       Can be empty initially.
        :param var_index: The index o the current variable which is handled in the iteration.
                          Should be zero in the first call.
        """

        # This method will do the following for each panel in the dashboard:
        #   It checks the first variable in the array of passed variable for its occurrence in the panels queries
        #   If the variable is needed, the first value is taken and the recursion goes one steep deeper to the
        #   second variable. There again the first value is taken if needed. Variables which are not part of the panel
        #   will be skipped. Once no more variables are left, the last one is completely iterated through.
        #   When the last one is finished, the second last moves to the second value and again the recursion heads to
        #   the last one to completely iterate through.

        if var_index == len(self.variables):
            # All variables have been checked if they are used for this panel,
            # if used, values are contained in params
            # if not, there may be none or there were no variables in the first place
            # this is the last iteration where the snapshot is created.
            _logger.debug(f'Dash: {self.uid} creating plot from `{self.current_panel["title"]}` with params {params}')
            slug = slugify(self.current_panel['title'])
            self.__save_png(name=os_path.join(dir, slug + '.png'),
                            params=params)
            return

        current_var = self.variables[var_index]
        do_var = False

        for target in self.current_panel['targets']:
            if current_var.name in target['expr']:
                # A panel can have multiple queries (targets), if one uses the variable it will be added
                do_var = True
                break

        if do_var:
            for val in current_var.values:
                # Append each variable value in a recursive call to the params
                # create a temporary _dir variable since for the current variable the parent directory should always
                # be the same.
                _dir = os_path.join(dir,
                                    slugify(val))
                Path(_dir).mkdir(exist_ok=True)
                params[f'var-{current_var.name}'] = val
                self.__rec_create_panel_plot(_dir,
                                             params,
                                             var_index + 1)
        else:
            # if the variable is not used the next one will be checked
            self.__rec_create_panel_plot(dir,
                                         params,
                                         var_index + 1)

    def __save_png(self,
                   name: str,
                   params: dict = None) -> None:
        """
        Requests the png plot of the current panel from grafana and saves it to disk

        :param name: The full path and name of the panel to save
        :param params: The parameters to pass to grafana when requesting the plot, this should be the variables
                       used in the query in form var-<VAR_NAME>=<VAR_VALUE>
        """

        if params is None:
            params = {}

        params['panelId'] = self.current_panel['id']

        # Add a custom height for graphs and timeseries so that all legend values are added for sure
        if self.current_panel['type'] == 'graph' or self.current_panel['type'] == 'timeseries':
            params['height'] = self.graph_height
            params['width'] = self.graph_width

        _logger.info(f'Creating {name}')
        result = self.grafana_client.d_solo_render(params=params,
                                                   dashboard_uid=self.uid,
                                                   dashboard_slug=self.slug)
        if result.ok:
            with open(name, 'wb') as png:
                png.write(result.content)
