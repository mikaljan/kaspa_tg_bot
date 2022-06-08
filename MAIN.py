# Import the kaspa client
from kaspy.kaspa_clients import RPCClient
from kaspy.utils.version_comparer import version as ver
import grpc


def get_stats(tries=0):
    if tries == 3:
        raise Exception
    cli = RPCClient()
    try:
        cli.auto_connect()
        # cli.auto_connect("81.70.100.207", 16110)
    except (Exception, grpc.RpcError) as e:
        print(e)
        cli.close()
        return get_stats(tries=tries + 1)
    try:
        stats = dict()
        blockdag_info = cli.request('getBlockDagInfoRequest', timeout=4)['getBlockDagInfoResponse']
        stats['block_count'] = blockdag_info['blockCount']
        stats['header_count'] = blockdag_info['headerCount']
        stats['pruning_point'] = blockdag_info['pruningPointHash']
        stats['parent_hashes'] = blockdag_info['virtualParentHashes']
        stats['tip_hashes'] = blockdag_info['tipHashes']
        stats['timestamp'] = blockdag_info['pastMedianTime']
        stats['difficulty'] = blockdag_info['difficulty']
        stats['hashrate'] = stats['difficulty'] * 2
        stats['daa_score'] = blockdag_info['virtualDaaScore']
    except (Exception, grpc.RpcError) as e:
        print(e)
        cli.close()
        return get_stats(tries=tries + 1)
    cli.close()
    return stats


print(get_stats())
