#!/usr/bin/env python

"""
Grafana Dashboard plotter entrypoint

@Licence: MIT
@Author: Boss Marco <bossm8@hotmail.com>
"""

from os import path as os_path
from time import time, strftime, localtime
from argparse import ArgumentParser
from multiprocessing import Pool, cpu_count
from grafana_api import GrafanaClient
from dashboard import Dashboard
from pathlib import Path
from yaml import safe_load, YAMLError

with open("config.yaml", "r") as config:
    try:
        _cfg = safe_load(config)
    except YAMLError as e:
        print("ERROR: Could not parse yaml configuration file")
        print(e)
        exit(1)

_grafana_client: GrafanaClient

_output_dir = os_path.join(
    Path(__file__).parent.resolve(),
    _cfg.get('plots', {'output_dir': 'plots'}).get('output_dir')
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


def run(sequential: bool = False):
    """
    Run the program to create plots of each dashboards panels.

    :param sequential: If the dashboards should be handled sequentially rather than concurrently
    """

    Path(_output_dir).mkdir(exist_ok=True)

    dashboards_c = _cfg.get('dashboards')

    if sequential:
        print('INFO: Handling dashboards sequentially')
        for dash in dashboards_c:
            plot_dashboard(dash)
    else:
        pool = Pool(cpu_count())
        pool.map(plot_dashboard, dashboards_c)
        pool.close()

    print('INFO: Plotting finished')


def main():
    current_time_s = time()
    cfg_from_s = _cfg.get('grafana').get('default_time_range', 3600)

    parser = ArgumentParser(description="Plot Grafana Dashboard Panels to png")
    parser.add_argument('-f', '--from',
                        help='The start of the time slice (unix timestamp) in which plots are created. '
                             'If not specified, the default from config.yaml or now-1h is used.',
                        dest='from_s',
                        default=current_time_s - cfg_from_s,
                        type=int)
    parser.add_argument('-t', '--to',
                        help='The end of the time slice (unix timestamp) which plots are created, defaults to now',
                        dest='to_s',
                        default=current_time_s,
                        type=int)
    parser.add_argument('-s', '--sequentially',
                        help='If dashboards should be handled in sequence rather than concurrently',
                        dest='seq',
                        action='store_true')

    args = vars(parser.parse_args())

    global _grafana_client
    _grafana_client = GrafanaClient(
        base_url=_cfg.get('grafana').get('base_url'),
        api_key=_cfg.get('grafana').get('admin_api_key'),
        from_ms=args['from_s'] * 1000,
        to_ms=args['to_s'] * 1000,
        node_exporter_job_name=_cfg.get('prometheus', {'node_exporter_job_name': 'node'}).get('node_exporter_job_name'),
        tls_verify=True if str(_cfg.get('grafana').get('tls_verify', 'true')).lower() in ['true', '1'] else False
    )

    print('INFO: Creating plots between {} and {}'.format(
        strftime('%X %x', localtime(args['from_s'])),
        strftime('%X %x', localtime(args['to_s']))
    ))
    run(sequential=args['seq'])


if __name__ == "__main__":
    main()
