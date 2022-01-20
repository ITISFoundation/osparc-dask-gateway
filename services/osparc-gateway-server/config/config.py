# defines the backend to use with the gateway

c.DaskGateway.backend_class = "osparc_gateway_server.backend.osparc.OsparcBackend"
# defines the password for 'simple' authentication
c.Authenticator.password = "asdf"
# defines log levels
c.DaskGateway.log_level = "WARN"
c.Proxy.log_level = "WARN"
# define how long to wait before cluster start up is deemed as errorred
c.DBBackendBase.cluster_start_timeout = 120.0
# define how long to wait before worker start up is deemed as errorred
c.DBBackendBase.worker_start_timeout = 120.0
