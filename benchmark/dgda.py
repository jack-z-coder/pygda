import sys
sys.path.append('..')

import torch
import argparse
import time
import os.path as osp
import numpy as np

from pygda.datasets import CitationDataset, TwitchDataset
from pygda.datasets import AirportDataset, MAGDataset, BlogDataset

from pygda.models import DGDA

from pygda.metrics import eval_micro_f1, eval_macro_f1, eval_roc_auc

from torch_geometric.loader import NeighborLoader
from torch_geometric.utils import degree, is_undirected, to_undirected
from torch_geometric.transforms import OneHotDegree

parser = argparse.ArgumentParser()

# model agnostic params
parser.add_argument('--seed', type=int, default=200, help='random seed')
parser.add_argument('--num_layers', type=int, default=2, help='number of layers')
parser.add_argument('--lr', type=float, default=0.001, help='learning rate')
parser.add_argument('--weight_decay', type=float, default=0.0, help='weight decay')
parser.add_argument('--nhid', type=int, default=64, help='hidden size')
parser.add_argument('--dropout_ratio', type=float, default=0.1, help='dropout ratio')
parser.add_argument('--device', type=str, default='cuda:1', help='specify cuda devices')
parser.add_argument('--source', type=str, default='DE', help='source domain data, DBLPv7/ACMv9/Citationv1')
parser.add_argument('--target', type=str, default='EN', help='target domain data, DBLPv7/ACMv9/Citationv1')
parser.add_argument('--epochs', type=int, default=200, help='maximum number of epochs')
parser.add_argument('--filename', type=str, default='test.txt', help='store results into file')

# model specific params
parser.add_argument('--dec_dim', type=int, default=64, help='dimension of the graph decoder hidden layer')
parser.add_argument('--d_dim', type=int, default=64, help='dimension of the domain latent variables')
parser.add_argument('--y_dim', type=int, default=256, help='dimension of the semantic latent variables')
parser.add_argument('--m_dim', type=int, default=128, help='dimension of the random latent variables')
parser.add_argument('--recons_w', type=float, default=1.0, help='trade-off weight for reconstruction loss')
parser.add_argument('--beta', type=float, default=0.5, help='trade-off weight for kl loss')
parser.add_argument('--ent_w', type=float, default=1.0, help='trade-off weight for entropy loss')
parser.add_argument('--d_w', type=float, default=1.0, help='trade-off weight for domain loss')
parser.add_argument('--y_w', type=float, default=1.0, help='trade-off weight for cross entropy loss')
parser.add_argument('--m_w', type=float, default=0.1, help='trade-off weight for manipulating reconstruction loss')

args = parser.parse_args()

# load data 
if args.source in {'DBLPv7', 'ACMv9', 'Citationv1'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/Citation', args.source)
    source_dataset = CitationDataset(path, args.source)
elif args.source in {'DE', 'EN', 'ES', 'FR', 'PT', 'RU'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/Twitch', args.source)
    source_dataset = TwitchDataset(path, args.source)
elif args.source in {'BRAZIL', 'USA', 'EUROPE'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/Airport', args.source)
    source_dataset = AirportDataset(path, args.source)
elif args.source in {'Blog1', 'Blog2'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/Blog', args.source)
    source_dataset = BlogDataset(path, args.source)
elif args.source in {'MAG_CN', 'MAG_DE', 'MAG_FR', 'MAG_JP', 'MAG_RU', 'MAG_US'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/MAG', args.source)
    source_dataset = MAGDataset(path, args.source)

if args.target in {'DBLPv7', 'ACMv9', 'Citationv1'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/Citation', args.target)
    target_dataset = CitationDataset(path, args.target)
elif args.target in {'DE', 'EN', 'ES', 'FR', 'PT', 'RU'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/Twitch', args.target)
    target_dataset = TwitchDataset(path, args.target)
elif args.target in {'BRAZIL', 'USA', 'EUROPE'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/Airport', args.target)
    target_dataset = AirportDataset(path, args.target)
elif args.target in {'Blog1', 'Blog2'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/Blog', args.target)
    target_dataset = BlogDataset(path, args.target)
elif args.target in {'MAG_CN', 'MAG_DE', 'MAG_FR', 'MAG_JP', 'MAG_RU', 'MAG_US'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/MAG', args.target)
    target_dataset = MAGDataset(path, args.target)

# construct node attributes for Airport dataset
max_degree = 0
for name in {'BRAZIL', 'USA', 'EUROPE'}:
    path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data/Airport', name)
    dataset = AirportDataset(path, name)
    data_degree = max(degree(dataset[0].edge_index[0,:]))
    if data_degree > max_degree:
        max_degree = data_degree

if args.source in {'BRAZIL', 'USA', 'EUROPE'}:
    target_dataset.transform = OneHotDegree(int(max_degree))
    source_dataset.transform = OneHotDegree(int(max_degree))

source_data = source_dataset[0].to(args.device)
target_data = target_dataset[0].to(args.device)

if args.source not in {'DE', 'EN', 'ES', 'FR', 'PT', 'RU'}:
    if not is_undirected(source_data.edge_index):
        source_data.edge_index = to_undirected(source_data.edge_index)
    
    if not is_undirected(target_data.edge_index):
        target_data.edge_index = to_undirected(target_data.edge_index)

num_features = source_data.x.size(1)
num_classes = len(np.unique(source_data.y.cpu().numpy()))

# choose a graph domain adaptation model
model = DGDA(
    in_dim=num_features,
    hid_dim=args.nhid,
    num_classes=num_classes,
    num_layers=args.num_layers,
    weight_decay=args.weight_decay,
    lr=args.lr,
    dropout=args.dropout_ratio,
    epoch=args.epochs,
    device=args.device,
    dec_dim=args.dec_dim,
    d_dim=args.d_dim,
    y_dim=args.y_dim,
    m_dim=args.m_dim,
    recons_w=args.recons_w,
    beta=args.beta,
    ent_w=args.ent_w,
    d_w=args.d_w,
    y_w=args.y_w,
    m_w=args.m_w
    )

# train the model
model.fit(source_data, target_data)

# evaluate the performance
logits, labels = model.predict(target_data)

maxvalue, maxindex = torch.max(logits, dim=1)

preds = logits.argmax(dim=1)

mi_f1 = eval_micro_f1(labels, preds)
ma_f1 = eval_macro_f1(labels, preds)

if args.source in {'DE', 'EN', 'ES', 'FR', 'PT', 'RU'}:
    auc = eval_roc_auc(labels, maxvalue)
else:
    auc = 0.0

results = 'dgda,source,' + args.source + ',target,' + args.target + ',micro-f1,' + str(mi_f1) + ',macro-f1,' + str(ma_f1) + ',auc,' + str(auc)

with open(args.filename, 'a+') as f:
    f.write(results + '\n')

print(results)