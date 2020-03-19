# (C) Datadog, Inc. 2020-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Sequence, cast

import rethinkdb

from datadog_checks.base import AgentCheck

from . import operations, queries
from .config import Config
from .document_db import DocumentQuery
from .document_db.types import Metric
from .types import Instance
from .version import parse_version


class RethinkDBCheck(AgentCheck):
    """
    Collect metrics from a RethinkDB cluster.
    """

    SERVICE_CHECK_CONNECT = 'rethinkdb.can_connect'

    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None
        super(RethinkDBCheck, self).__init__(*args, **kwargs)
        self.config = Config(cast(Instance, self.instance))
        self.queries = (
            queries.config_summary,
            queries.cluster_statistics,
            queries.server_statistics,
            queries.table_statistics,
            queries.replica_statistics,
            queries.table_statuses,
            queries.server_statuses,
            queries.system_jobs,
            queries.current_issues_summary,
        )  # type: Sequence[DocumentQuery]

    @contextmanager
    def connect_submitting_service_checks(self):
        # type: () -> Iterator[rethinkdb.net.Connection]
        config = self.config

        tags = ['host:{}'.format(config.host), 'port:{}'.format(config.port)]
        tags.extend(config.tags)

        try:
            with rethinkdb.r.connect(
                host=config.host,
                port=config.port,
                user=config.user,
                password=config.password,
                ssl={'ca_certs': config.tls_ca_cert} if config.tls_ca_cert is not None else None,
            ) as conn:
                yield conn
        except rethinkdb.errors.ReqlDriverError as exc:
            message = 'Could not connect to RethinkDB server: {!r}'.format(exc)
            self.log.error(message)
            self.service_check(self.SERVICE_CHECK_CONNECT, self.CRITICAL, tags=tags, message=message)
            raise
        except Exception as exc:
            message = 'Unexpected error while executing RethinkDB check: {!r}'.format(exc)
            self.log.error(message)
            self.service_check(self.SERVICE_CHECK_CONNECT, self.CRITICAL, tags=tags, message=message)
            raise
        else:
            self.service_check(self.SERVICE_CHECK_CONNECT, self.OK, tags=tags)

    def collect_metrics(self, conn):
        # type: (rethinkdb.net.Connection) -> Iterator[Metric]
        """
        Collect metrics from the RethinkDB cluster we are connected to.
        """
        for query in self.queries:
            for metric in query.run(conn, logger=self.log):
                yield metric

    def collect_connected_server_version(self, conn):
        # type: (rethinkdb.net.Connection) -> str
        """
        Return the version of RethinkDB run by the server at the other end of the connection, in SemVer format.
        """
        version_string = operations.get_connected_server_version_string(conn)
        return parse_version(version_string)

    def submit_metric(self, metric):
        # type: (Metric) -> None
        submit = getattr(self, metric['type'])  # type: Callable
        submit(metric['name'], metric['value'], tags=self.config.tags + metric['tags'])

    def submit_version_metadata(self, conn):
        # type: (rethinkdb.net.Connection) -> None
        try:
            version = self.collect_connected_server_version(conn)
        except ValueError as exc:
            self.log.error('Error collecting version metadata: %r', exc)
        else:
            self.set_metadata('version', version)

    def check(self, instance):
        # type: (Any) -> None
        with self.connect_submitting_service_checks() as conn:
            for metric in self.collect_metrics(conn):
                self.submit_metric(metric)

            if self.is_metadata_collection_enabled():
                self.submit_version_metadata(conn)
