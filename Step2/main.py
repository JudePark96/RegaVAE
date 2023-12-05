import argparse
import logging
import os
import json
import torch
import random
import numpy as np
import pandas as pd
import time
from autofaiss import build_index

from torch.nn.parallel import DataParallel
from torch.utils.data import DataLoader

from dataset import VAEDataset, WPDataset
from train import train, valid, generate,test
from tqdm import tqdm

from model import RegaVAE
from cmodel import CRegaVAE
from fastbm25 import fastbm25
import pickle

from transformers import AutoConfig, AutoModel, AutoTokenizer

logger = logging.getLogger(__name__)

stoplist = ['very', 'ourselves', 'am', 'doesn', 'through', 'me', 'against', 'up', 'just', 'her', 'ours', 
            'couldn', 'because', 'is', 'isn', 'it', 'only', 'in', 'such', 'too', 'mustn', 'under', 'their', 
            'if', 'to', 'my', 'himself', 'after', 'why', 'while', 'can', 'each', 'itself', 'his', 'all', 'once', 
            'herself', 'more', 'our', 'they', 'hasn', 'on', 'ma', 'them', 'its', 'where', 'did', 'll', 'you', 
            'didn', 'nor', 'as', 'now', 'before', 'those', 'yours', 'from', 'who', 'was', 'm', 'been', 'will', 
            'into', 'same', 'how', 'some', 'of', 'out', 'with', 's', 'being', 't', 'mightn', 'she', 'again', 'be', 
            'by', 'shan', 'have', 'yourselves', 'needn', 'and', 'are', 'o', 'these', 'further', 'most', 'yourself', 
            'having', 'aren', 'here', 'he', 'were', 'but', 'this', 'myself', 'own', 'we', 'so', 'i', 'does', 'both', 
            'when', 'between', 'd', 'had', 'the', 'y', 'has', 'down', 'off', 'than', 'haven', 'whom', 'wouldn', 
            'should', 've', 'over', 'themselves', 'few', 'then', 'hadn', 'what', 'until', 'won', 'no', 'about', 
            'any', 'that', 'for', 'shouldn', 'don', 'do', 'there', 'doing', 'an', 'or', 'ain', 'hers', 'wasn', 
            'weren', 'above', 'a', 'at', 'your', 'theirs', 'below', 'other', 'not', 're', 'him', 'during', 'which','_unk']
def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train_file", default='../data/yelp/yelp.train.txt', type=str,
                        help="Data path for training.")
    parser.add_argument("--valid_file", default='../data/yelp/yelp.valid.txt', type=str,
                        help="Data path for valid")
    parser.add_argument("--test_file", default='../data/yelp/yelp.test.txt', type=str,
                        help="Data path for test")
    
    parser.add_argument("--train_source_path", default='../data/writingPrompts/train.wp_source', type=str,
                        help="Data path for training.")
    parser.add_argument("--train_target_path", default='../data/writingPrompts/train.wp_target', type=str,
                        help="Data path for valid")
    parser.add_argument("--valid_source_path", default='../data/writingPrompts/valid.wp_source ', type=str,
                        help="Data path for test")
    parser.add_argument("--valid_target_path", default='../data/writingPrompts/valid.wp_target', type=str,
                        help="Data path for training.")
    parser.add_argument("--test_source_path", default='.', type=str,
                        help="Data path for valid")
    parser.add_argument("--test_target_path", default='.', type=str,
                        help="Data path for test")
    parser.add_argument("--pretrained_model", type=str, default='gpt2', 
                        help="Pretrained model to be loaded")
    parser.add_argument("--dataset_type", type=str, default='vae', choices=['vae', 'wp'], 
                        help="Dataset type")
    parser.add_argument("--output_dir", default='./checkpoints', type=str,
                        help="The output directory where the model checkpoints and predictions will be written.")
    parser.add_argument("--model_name", default='regavae', type=str,
                        help="The model name")
    parser.add_argument("--generation_output_dir", default='./generation_output', type=str,
                        help="The output directory where the log will be written.")
    # Other parameters\
    parser.add_argument("--load_epoch", default=-1, type=int, help="the epochs of trained model to load")
    parser.add_argument("--epochs", default=40, type=int, help="total epochs")
    parser.add_argument("--per_gpu_train_batch_size", default=4, type=int,help="Batch size per GPU for training.")
    parser.add_argument("--no_cuda", action='store_true',
                        help="Whether not to use CUDA when available")
    parser.add_argument("--rebulid_bm25_index", action='store_true',
                        help="about 20h")
    parser.add_argument("--learning_rate", default=5e-5, type=float, help="The initial learning rate for Adam.")
    parser.add_argument('--gradient_accumulation_steps', type=int, default=8,
                        help="Number of updates steps to accumulate before performing a backward/update pass.")
    parser.add_argument("--weight_decay", default=0.01, type=float,
                        help="Weight decay if we apply some.")
    parser.add_argument("--adam_epsilon", default=1e-8, type=float,
                        help="Epsilon for Adam optimizer.")
    parser.add_argument("--kl_threshold", default=0, type=float,
                        help="The threshold of the minimum KL value, default as 0")
    parser.add_argument("--latent_size", default=32, type=int,
                        help="The dimension of latent space")
    parser.add_argument("--latent_lmf_rank", default=4, type=int,
                        help="latent size")
    parser.add_argument("--max_length", default=200, type=int,
                        help="Max length for generation")
    parser.add_argument("--rebuild_index_step", default=200, type=int)
    parser.add_argument("--retrieve_rate", default=0.1, type=float)
    parser.add_argument("--neighbors", default=5, type=int)
    parser.add_argument("--bm25_epoch", default=0, type=int)
    parser.add_argument('--seed', type=int, default=42,
                        help="Random seed for initialization")
    parser.add_argument('--log_step', type=int, default=100,
                        help="Steps for logging")
    parser.add_argument('--num_beams', type=int, default=10,
                        help="Beam size for searching")
    parser.add_argument('--greedy_decoding', action='store_true',
                        help="Choose to use greedy decoding")
    parser.add_argument('--top_k', type=int, default=-1, help='Set top k')
    parser.add_argument('--top_p', type=float, default=0.9, help='Set top p')
    parser.add_argument('--repetition_penalty', type=float, default=1.2)
    parser.add_argument('--model_parallel', action='store_true', 
                        help="Choose to use model parallel, mapping the layers to different devices")
    parser.add_argument('--eval', action='store_true', help='Choose to eval the model')
    parser.add_argument('--eval_metrics', action='store_true',
                        help="Choose to eval the metrics for representation learning")
    parser.add_argument('--generation', action='store_true', help='Choose to generate')
    parser.add_argument('--use_scheduler', action='store_true',
                        help="Choose to use lr scheduler")
    parser.add_argument('--cycle_annealing', action='store_true',
                        help="Choose to use cycle annealing")
    parser.add_argument('--cycle_iters', type=int, default=2,
                        help="Set the iters for cycle annealing")
    parser.add_argument('--sample_times', type=int, default=30,
                        help="The total times of sample when computing PPL with importance weighted sampling")
    parser.add_argument('--use_bow', action='store_true',
                        help="Choose to use bow loss")
    parser.add_argument('--bow_weight',type=float, default=0.2,
                        help="Set the weight of bow loss term")
    parser.add_argument("--begin_layer", default=None, type=int,
                        help="The beginning layer to consider the latent vector, default as the first layer of model")
    parser.add_argument("--end_layer", default=None, type=int,
                        help="The end layer to consider the latent vector, default as the last layer of model")
    args = parser.parse_args()
    return args

def prepare(args):
    torch.set_num_threads(3)

    if not args.eval and not args.generation:
        os.makedirs(os.path.join(args.output_dir, args.model_name), exist_ok=True)
        json.dump(args.__dict__, open(os.path.join(
            args.output_dir, args.model_name, 'train_opt.json'), 'w'), sort_keys=True, indent=2)

    if args.no_cuda:
        args.n_gpu = 1
    else:
        args.n_gpu = torch.cuda.device_count()
    args.batch_size = args.per_gpu_train_batch_size * args.n_gpu
    
    # Setup logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(filename)s[line:%(lineno)d] - %(levelname)s: %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    # Set seed
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    if args.n_gpu > 0:
        torch.cuda.manual_seed_all(args.seed)

    logger.info("Training/evaluation parameters %s", args)

    if args.no_cuda:
        args.device = torch.device('cpu')
    else:
        args.device = torch.device('cuda:0')

def init_para_frompretrained(model, gpt2):
    logger.info('load gpt2 pretrained model parameters')
    model = model.encoder
    model.wte.weight = gpt2.wte.weight
    model.wpe.weight = gpt2.wpe.weight

    for i in range(len(gpt2.h)):
        model.h[i].ln_1.weight = gpt2.h[i].ln_1.weight
        model.h[i].ln_1.bias = gpt2.h[i].ln_1.bias
        model.h[i].attn.c_attn.weight = gpt2.h[i].attn.c_attn.weight
        model.h[i].attn.c_attn.bias = gpt2.h[i].attn.c_attn.bias
        model.h[i].attn.c_proj.weight = gpt2.h[i].attn.c_proj.weight
        model.h[i].attn.c_proj.bias = gpt2.h[i].attn.c_proj.bias
        model.h[i].ln_2.weight = gpt2.h[i].ln_2.weight
        model.h[i].ln_2.bias = gpt2.h[i].ln_2.bias
        model.h[i].mlp.c_fc.weight = gpt2.h[i].mlp.c_fc.weight
        model.h[i].mlp.c_fc.bias = gpt2.h[i].mlp.c_fc.bias
        model.h[i].mlp.c_proj.weight = gpt2.h[i].mlp.c_proj.weight
        model.h[i].mlp.c_proj.bias = gpt2.h[i].mlp.c_proj.bias

    model.ln_f.weight = gpt2.ln_f.weight
    model.ln_f.bias = gpt2.ln_f.bias

def prepare_model(args):
    tokenizer = AutoTokenizer.from_pretrained(args.pretrained_model)
    if '<s>' not in tokenizer.vocab:
        tokenizer._add_tokens(['<s>'])
    if '</s>' not in tokenizer.vocab:
        tokenizer._add_tokens(['</s>'])
    tokenizer.pad_id = 50256
    
    tokenizer.bos_id = tokenizer.convert_tokens_to_ids('<s>')
    tokenizer.eos_id = tokenizer.convert_tokens_to_ids('</s>')

    model_config = AutoConfig.from_pretrained(args.pretrained_model)
    model_config.vocab_size = len(tokenizer)
    model_config.pad_token_id = tokenizer.pad_id
    model_config.kl_threshold = args.kl_threshold
    model_config.is_cvae = (args.dataset_type == 'wp')
    model_config.use_bow = args.use_bow
    model_config.begin_layer = args.begin_layer
    model_config.end_layer = args.end_layer

    for arg in vars(args):
        if arg.startswith('latent'):
            setattr(model_config, arg, getattr(args, arg))
    if args.dataset_type == 'vae':
        model = RegaVAE(model_config,args)
    else:
        model = CRegaVAE(model_config,args)
    
    pretrained_model = AutoModel.from_pretrained(args.pretrained_model)
    logging.info('loading pretrained model parameters...')
    init_para_frompretrained(model, pretrained_model)
    model.encoder.resize_token_embeddings(len(tokenizer))
    model.decoder.wte = model.encoder.wte
    if args.load_epoch is not None:
        model_path = os.path.join(args.output_dir, args.model_name, 'model_epoch_{}.pt'.format(args.load_epoch))
        model_state_dict = torch.load(model_path, map_location=args.device)
        model.load_state_dict(model_state_dict)
        logging.info('load model_epoch_{}.pt finish'.format(args.load_epoch))
    else:
        args.load_epoch = -1

    if args.model_parallel and torch.cuda.device_count() > 1:  
        logging.info('model paralleize...')
        model.parallelize()
    else:
        model = model.to(args.device)
        if torch.cuda.device_count() > 1:
            model = DataParallel(model)
    return model, tokenizer

def prepare_data(tokenizer, args):
    dataset_class = {'vae': VAEDataset, 'wp': WPDataset}
    if args.eval or args.generation:
        logging.info("eval model: the epoch {} of {}".format(args.load_epoch, args.model_name))
        if args.dataset_type == 'vae':
            test_dataset = dataset_class[args.dataset_type](args.test_file, tokenizer, args.device)
        else:
            test_dataset = dataset_class[args.dataset_type](args.test_source_path, args.test_target_path,tokenizer, args.device)
        test_iter = DataLoader(test_dataset, batch_size=args.batch_size, collate_fn=test_dataset.collate_fn)
        return test_iter
    else:
        if args.dataset_type == 'vae':
            train_dataset = dataset_class[args.dataset_type](args.train_file, tokenizer, args.device)
            valid_dataset = dataset_class[args.dataset_type](args.valid_file, tokenizer, args.device)
        else:
            train_dataset = dataset_class[args.dataset_type](args.train_source_path, args.train_target_path,tokenizer, args.device)
            valid_dataset = dataset_class[args.dataset_type](args.valid_source_path, args.valid_target_path, tokenizer, args.device)
        train_iter = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=train_dataset.collate_fn)
        valid_iter = DataLoader(valid_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=valid_dataset.collate_fn)
        logging.info('training with {} samples...'.format(len(train_dataset)))
        return train_iter, valid_iter

def main():
    def get_logger(filename):
        # from logging import getLogger, INFO, StreamHandler, FileHandler, Formatter
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.INFO)
        handler1 = logging.StreamHandler()
        handler1.setFormatter(logging.Formatter("%(message)s"))
        handler2 = logging.FileHandler(filename=f"{filename}.log")
        handler2.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler1)
        logger.addHandler(handler2)
        return logger

    
    args = get_args()
    LOGGER = get_logger(os.path.join(args.output_dir,'train'))
    prepare(args)
    model, tokenizer = prepare_model(args)
    total_params = sum(p.numel() for p in model.parameters())
    logging.info('total parameters: {}'.format(total_params))
    
    
    if args.eval or args.generation:
        train_dataset = VAEDataset(args.train_file,tokenizer, args.device)
        df = pd.DataFrame({'text':train_dataset.data})
        test_iter = prepare_data(tokenizer, args)
        if args.eval:
            test(model,test_iter,args,df,tokenizer,LOGGER)
        if args.generation:
            generate(model, test_iter, tokenizer, args,df)
    else:
        if args.dataset_type == 'vae':
            train_dataset = VAEDataset(args.train_file,tokenizer, args.device)
            valid_dataset = VAEDataset(args.valid_file,tokenizer, args.device)

            train_iter = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=train_dataset.collate_fn)
            valid_iter = DataLoader(valid_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=valid_dataset.collate_fn)
            if args.rebulid_bm25_index:
                corpus = [doc.lower() for doc in train_dataset.data]
                for i in corpus:
                    for j in stoplist:
                        i=i.replace(j,'')
                tokenized_corpus = [doc.split() for doc in corpus]
                bm25 = fastbm25(tokenized_corpus)
                result_indexs=[]
                result_scores=[]
                for i in tqdm(corpus):
                    result = bm25.top_k_sentence(i.lower().split(),k=101)
                    result_indexs.append([tmp_index[1] for tmp_index in result[1:]])
                    result_scores.append([tmp_index[2] for tmp_index in result[1:]])
                df = pd.DataFrame({'text':corpus,'result_indexs':result_indexs,'result_scores':result_scores})

                corpus = [doc.lower() for doc in valid_dataset.data]
                for i in corpus:
                    for j in stoplist:
                        i=i.replace(j,'')
                result_indexs=[]
                result_scores=[]
                for i in tqdm(corpus):
                    result = bm25.top_k_sentence(i.lower().split(),k=101)
                    result_indexs.append([tmp_index[1] for tmp_index in result[1:]])
                    result_scores.append([tmp_index[2] for tmp_index in result[1:]])
                df_valid = pd.DataFrame({'text':corpus,'result_indexs':result_indexs,'result_scores':result_scores})
            else:
                df = pickle.load(file=open('df_bm25.csv', 'rb'))
                df['text'] = train_dataset.data
                df_valid = pickle.load(file=open('df_bm25_valid.csv', 'rb'))
                df_valid['text'] = valid_dataset.data
        else:
            train_dataset = WPDataset(args.train_source_path, args.train_target_path,tokenizer, args.device)
            valid_dataset = WPDataset(args.valid_source_path, args.valid_target_path, tokenizer, args.device)
            train_iter = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=train_dataset.collate_fn)
            valid_iter = DataLoader(valid_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=valid_dataset.collate_fn)
            df = pd.DataFrame({'text':train_dataset.source})
            df_valid = df
        train(model, train_iter, valid_iter, args,df,df_valid,tokenizer,LOGGER)
            

if __name__ == "__main__":
    main()
