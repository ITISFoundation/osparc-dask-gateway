from dask_gateway_server.options import Float, Integer, Options

# def options_handler(options):
#     return {
#         "worker_cores": options.worker_cores,
#         "cluster_max_cores": options.cluster_max_cores,
#         "worker_memory": int(options.worker_memory * 2 ** 30),

#     }

# c.Backend.cluster_options = Options(
#     Integer("cluster_max_cores", default=1, min=1, max=12, label="Cluster Cores"),
#     Integer("worker_cores", default=1, min=1, max=4, label="Worker Cores"),
#     Float("worker_memory", default=1, min=1, max=8, label="Worker Memory (GiB)"),
#     handler=options_handler,
# )

c.Backend.cluster_config_class = (
    "osparc_gateway_server.backend.osparc.OsparcClusterConfig"
)
c.DaskGateway.backend_class = "osparc_gateway_server.backend.osparc.UnsafeOsparcBackend"
c.OsparcBackend.clusters_directory = "/mnt/gateway"
# c.OsparcBackend.run_on_host = True
# c.OsparcBackend.run_in_swarm = False
# c.OsparcBackend.default_host = '172.16.8.64'
# c.Backend.cluster_config_class = 'dask_gateway_server.backends.local.LocalClusterConfig'
# c.DaskGateway.backend_class = 'dask_gateway_server.backends.local.UnsafeLocalBackend'
c.Authenticator.password = "asdf"
c.DaskGateway.log_level = "DEBUG"
# c.Proxy.tls_cert = "/mnt/gateway/.certs/dask.crt"
# c.Proxy.tls_key = "/mnt/gateway/.certs/dask.pem"
