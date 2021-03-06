import ipaddress
import os
import re
import socket
from contextlib import closing
from datetime import datetime

import grequests
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

webhook = os.getenv("WEBHOOK")
rpc = os.getenv("BASE_RPC")

logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p', level=logging.INFO)
tmp = []
rpcs = []
scanned = []
nodes = {}
blacklist = []

# Get Chainid
net_req = requests.get(rpc + "/status", timeout=20)
net = net_req.json()
chainid = net['result']['node_info']['network']


def is_ip_private(ip):
    # https://en.wikipedia.org/wiki/Private_network

    priv_lo = re.compile("^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    priv_24 = re.compile("^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    priv_20 = re.compile("^192\.168\.\d{1,3}.\d{1,3}$")
    priv_16 = re.compile("^172.(1[6-9]|2[0-9]|3[0-1]).[0-9]{1,3}.[0-9]{1,3}$")

    res = priv_lo.match(ip) or priv_24.match(ip) or priv_20.match(ip) or priv_16.match(ip)
    return res is not None


def parse_connections_for_node(node):
    net_check_req = requests.get(node + "/status", timeout=20)
    net_check = net_check_req.json()
    if net_check['result']['node_info']['network'] != chainid:  # Check if node is on the right chain
        blacklist.append(node)
        rpcs.remove(node)
        tmp.remove(node)
        logging.info(f"Node {node} is on the wrong network")
        return
    elif node in blacklist:
        return

    net_info_req = requests.get(node + "/net_info", timeout=20)
    net_info = net_info_req.json()

    # Go through the peers in the net_info peer section
    for peer in net_info['result']['peers']:
        ip = peer['remote_ip']
        id = peer['node_info']['id']
        listenaddr = peer['node_info']['listen_addr']
        p2p_ip_raw = listenaddr[6:]
        p2p_ip_splitted = p2p_ip_raw.split(':')
        # If the p2p address is set to 0.0.0.0 take the external ip
        if p2p_ip_splitted[0] == "0.0.0.0":
            p2p_con = ip + ":" + p2p_ip_splitted[1]
            if ip not in nodes.keys() and not is_ip_private(ip):
                nodes[ip] = {
                    'id': id,
                    'port': p2p_ip_splitted[1],
                    'connection_string': id + "@" + p2p_con
                }
        # If the p2p address isn't a private one take it and insert it
        elif not is_ip_private(p2p_ip_splitted[0]):
            p2p_con = ip + ":" + p2p_ip_raw
            if ip not in nodes.keys():
                nodes[ip] = {
                    'id': id,
                    'port': p2p_ip_splitted[1],
                    'connection_string': id + "@" + p2p_con
                }
        logging.info(f"Current Node list length {len(nodes)}")

        # See if a rpc_address is set. If it's not a internal one take the port and the external ip and add it to the list
        rpc_str = peer['node_info']['other']['rpc_address'][6:] if 'other' in peer['node_info'] and 'rpc_address' in \
                                                                   peer['node_info']['other'] else None
        rpc = rpc_str.split(':')
        if rpc_str and not is_ip_private(rpc[0]):
            if f"http://{ip}:{rpc[1]}" not in rpcs and f"http://{ip}:{rpc[1]}" not in scanned:
                tmp.append(f"http://{ip}:{rpc[1]}")
                logging.info(f"Adding http://{ip}:{rpc[1]} to the checks")

    rs = (grequests.get(u, timeout=5) for u in tmp)
    responses = grequests.map(rs)
    scanned.extend(tmp)
    tmp.clear()
    for response in responses:
        if response is not None and response.status_code == 200:
            if response.url not in rpcs and response.url not in blacklist:
                logging.info(f"Starting Lookup for {response.url}")
                rpcs.append(response.url)
                try:
                    parse_connections_for_node(response.url)
                except:
                    pass


def send_rpcs_to_discord():
    message = f"Discovered RPC endpoints:\n```" + '\n'.join(rpcs) + "```"
    x = requests.post(url=webhook, data={'content': message}, headers={
        'Content-Type': 'application/x-www-form-urlencoded',
    })


def send_peers_to_discord():
    nodes_list = [x['connection_string'] for x in nodes.values()]
    splitted = chunks(nodes_list, 30)
    c = 1
    for nlist in splitted:
        message = f"Discovered Peers - **Batch {c}**\n```" + ','.join(nlist) + "```"
        x = requests.post(url=webhook, data={'content': message}, headers={
            'Content-Type': 'application/x-www-form-urlencoded',
        })
        c += 1


def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


if __name__ == "__main__":
    parse_connections_for_node(rpc)
    send_peers_to_discord()
    send_rpcs_to_discord()
    logging.info(f"Synced all {len(nodes)} nodes from {len(rpcs)} rpcs")
