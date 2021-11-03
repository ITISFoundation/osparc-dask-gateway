from dask_gateway import Gateway, BasicAuth
auth = BasicAuth(username=None, password="asdf")
gateway = Gateway(address="http://172.16.8.64:8000", auth=auth)
cluster = gateway.new_cluster()

cluster2 = gateway.new_cluster()

cluster.scale(1)
cluster2.scale(1)

client = cluster.get_client()
client2 = cluster2.get_client()

def square(x):
    return x ** 2

def neg(x):
    return -x


A = client.map(square, range(10))
B = client.map(neg, A)

A2 = client2.map(square, range(10))
B2 = client2.map(neg, A2)

total = client.submit(sum, B)
print("Cluster 1", total.result())

total2 = client2.submit(sum, B2)
print("Cluster 2", total2.result())
import time
while True:
    time.sleep(5)

