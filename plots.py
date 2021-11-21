"""
Grafana Dashboard plotter entrypoint

@Licence: MIT
@Author: Boss Marco <bossm8@hotmail.com>
"""

from os import path as os_path
from time import time_ns, time, strftime, localtime
from argparse import ArgumentParser
from confuse import Configuration
from multiprocessing import Pool, cpu_count
from grafana_api import GrafanaClient
from dashboard import Dashboard
from pathlib import Path

_grafana_client: GrafanaClient

_config = Configuration('GrafanaDashboardPlotter', __name__)
_config.set_file('config.yaml')

_current_time_ms = int(time_ns() // 1_000_000)

try:
    _from = _current_time_ms - (_config['grafana']['default_time_slice'].get(int) * 1000)
except:
    print('INFO: no (correct) time slice specified, using 3600s (now-1h as start)')
    _from = _current_time_ms - (3600 * 1000)

try:
    _output_dir = _config['plots']['output_dir'].as_path()
except:
    print("INFO: No (correct) output path specified, using './plots'")
    _output_dir = 'plots'

_output_dir = os_path.join(
    Path(__file__).parent.resolve(),
    _output_dir
)


def plot_dashboard(dash_config: dict):
    """
    Plot all panels of one dashboard.

    :param dash_config: The configuration of the dashboard read from config.yaml
    """

    if 'variables' not in dash_config:
        dash_config['variables'] = []

    dashboard = Dashboard(_grafana_client,
                          dash_config['uid'],
                          dash_config['variables'])
    dashboard.create_plots(_output_dir)


def run(concurrent: bool):
    """
    Run the program to create plots of each dashboards panels.

    :param concurrent: If the dashboards should be handled concurrently
    """

    dashboards_c = _config['dashboards'].get(list)

    if concurrent:
        pool = Pool(cpu_count())
        pool.map(plot_dashboard, dashboards_c)
        pool.close()
    else:
        for dash in dashboards_c:
            plot_dashboard(dash)

    print('INFO: Plotting finished')


def main():
    parser = ArgumentParser(description="Plot Grafana Dashboard Panels to png")
    parser.add_argument('-f', '--from',
                        help='The start of the time slice (unix timestamp) in which plots are created. '
                             'If not specified, the default from config.yaml or now-1h is used.',
                        dest='from',
                        type=int)
    parser.add_argument('-t', '--to',
                        help='The end of the time slice (unix timestamp) which plots are created, defaults to now',
                        dest='to',
                        default=int(time()),
                        type=int)
    parser.add_argument('-s', '--sequentially',
                        help='If dashboards should be handled in sequence rather than concurrently',
                        dest='seq',
                        action='store_true')

    args = vars(parser.parse_args())

    Path(_output_dir).mkdir(exist_ok=True)

    global _grafana_client, _from
    _grafana_client = GrafanaClient(
        base_url=_config['grafana']['base_url'].get(str),
        api_key=_config['grafana']['admin_api_key'].get(str),
        _from=int(args['from'] * 1000) if args['from'] else _from,
        _to=int(args['to'] * 1000),
        node_exporter_job_name=_config['prometheus']['node_exporter_job_name'].get(str)
    )

    print('INFO: Creating plots between {} and {}'.format(
        strftime('%X %x %Z', localtime(int(args['from']) if args['from'] else _from/1000)),
        strftime('%X %x %Z', localtime(int(args['to'])))
    ))
    run(concurrent=False if args['seq'] else True)


if __name__ == "__main__":
    main()
