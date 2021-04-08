import os
import pathlib
from datetime import datetime

import imageio
import numpy as np
import torch
import yaml
from kpt.model.skeleton import TorchSkeleton
from PIL import Image
from pymo.parsers import BVHParser
from torch.utils.data import DataLoader

from rmi.data.lafan1_dataset import LAFAN1Dataset
from rmi.model.network import Decoder, InputEncoder, LSTMNetwork
from rmi.model.positional_encoding import PositionalEncoding
from rmi.vis.pose import plot_pose


def test():
    # Load configuration from yaml
    config = yaml.safe_load(open('./config/config_base.yaml', 'r').read())

    # Set device to use
    gpu_id = config['device']['gpu_id']
    device = torch.device(f"cuda:{gpu_id}" if torch.cuda.is_available() else "cpu")

    # Prepare Directory
    time_stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    saved_weight_path = config['model']['saved_weight_path']
    result_path = os.path.join('results', time_stamp)
    result_gif_path = os.path.join(result_path, 'gif')
    pathlib.Path(result_gif_path).mkdir(parents=True, exist_ok=True)

    # Load Skeleton
    parsed = BVHParser().parse(config['data']['skeleton_path'])
    skeleton = TorchSkeleton(skeleton=parsed.skeleton, root_name='Hips', device=device)

     # Load and preprocess data. It utilizes LAFAN1 utilities
    lafan_dataset_test = LAFAN1Dataset(lafan_path=config['data']['data_dir'], train=False, device=device)
    lafan_data_loader_test = DataLoader(lafan_dataset_test, batch_size=config['model']['batch_size'], shuffle=False, num_workers=config['data']['data_loader_workers'])

    inference_batch_index = config['test']['inference_batch_index']

    # Extract dimension from processed data
    root_v_dim = lafan_dataset_test.root_v_dim
    local_q_dim = lafan_dataset_test.local_q_dim
    contact_dim = lafan_dataset_test.contact_dim

    # Initializing networks
    state_in = root_v_dim + local_q_dim + contact_dim
    state_encoder = InputEncoder(input_dim=state_in)
    state_encoder.to(device)
    state_encoder.load_state_dict(torch.load(os.path.join(saved_weight_path, 'state_encoder.pkl'), map_location=device))

    offset_in = root_v_dim + local_q_dim
    offset_encoder = InputEncoder(input_dim=offset_in)
    offset_encoder.to(device)
    offset_encoder.load_state_dict(torch.load(os.path.join(saved_weight_path, 'offset_encoder.pkl'), map_location=device))

    target_in = local_q_dim
    target_encoder = InputEncoder(input_dim=target_in)
    target_encoder.to(device)
    target_encoder.load_state_dict(torch.load(os.path.join(saved_weight_path, 'target_encoder.pkl'), map_location=device))

    # LSTM
    lstm_in = state_encoder.out_dim * 3 + 2
    lstm = LSTMNetwork(input_dim=lstm_in, hidden_dim=lstm_in*2, device=device)
    lstm.to(device)
    lstm.load_state_dict(torch.load(os.path.join(saved_weight_path, 'lstm.pkl'), map_location=device))

    # Decoder
    decoder = Decoder(input_dim=lstm_in*2, out_dim=state_in)
    decoder.to(device)
    decoder.load_state_dict(torch.load(os.path.join(saved_weight_path, 'decoder.pkl'), map_location=device))

    pe = PositionalEncoding(dimension=256, max_len=lafan_dataset_test.cur_seq_length)

    print("MODELS LOADED WITH SAVED WEIGHTS")

    state_encoder.eval()
    offset_encoder.eval()
    target_encoder.eval()
    lstm.eval()
    decoder.eval()
    
    for i_batch, sampled_batch in enumerate(lafan_data_loader_test):
        img_gt = []
        img_pred = []
        img_integrated = []

        current_batch_size = len(sampled_batch['global_pos'])

        with torch.no_grad():
            # state input
            local_q = sampled_batch['local_q'].to(device)
            root_v = sampled_batch['root_v'].to(device)
            contact = sampled_batch['contact'].to(device)
            # offset input
            root_p_offset = sampled_batch['root_p_offset'].to(device)
            local_q_offset = sampled_batch['local_q_offset'].to(device)
            local_q_offset = local_q_offset.view(current_batch_size, -1)
            # target input
            target = sampled_batch['q_target'].to(device)
            target = target.view(current_batch_size, -1)
            # root pos
            root_p = sampled_batch['root_p'].to(device)
            # global pos
            global_pos = sampled_batch['global_pos'].to(device)

            # Motion conditioning
            motion_condition = sampled_batch['seq_names'].to(device)

            lstm.init_hidden(current_batch_size)

            # Generating Frames. It uses fixed 50 frames of generation for now.
            for t in range(lafan_dataset_test.cur_seq_length - 1): # cur seq length = 50
                # root pos
                if t  == 0:
                    root_p_t = root_p[:,t]
                    root_v_t = root_v[:,t]
                    local_q_t = local_q[:,t]
                    local_q_t = local_q_t.view(local_q_t.size(0), -1)
                    contact_t = contact[:,t]
                else:
                    root_p_t = root_pred  # Be careful about dimension
                    root_v_t = root_v_pred[0]
                    local_q_t = local_q_pred[0]
                    contact_t = contact_pred[0]
                    
                assert root_p_offset.shape == root_p_t.shape

                # state input
                state_input = torch.cat([local_q_t, root_v_t, contact_t], -1)
                # offset input
                root_p_offset_t = root_p_offset - root_p_t
                local_q_offset_t = local_q_offset - local_q_t
                offset_input = torch.cat([root_p_offset_t, local_q_offset_t], -1)
                # target input
                target_input = target
                
                h_state = state_encoder(state_input)
                h_offset = offset_encoder(offset_input)
                h_target = target_encoder(target_input)
                
                # Use positional encoding
                h_state = pe(h_state, t)
                h_offset = pe(h_offset, t)
                h_target = pe(h_target, t)

                offset_target = torch.cat([h_offset, h_target], dim=1)

                # lstm
                motion_condition[:, t, 0] = 1 * 3
                motion_condition[:, t, 1] = 1 * 3
                h_in = torch.cat([h_state, offset_target, motion_condition[:, t]], dim=1).unsqueeze(0)
                h_out = lstm(h_in)
            
                # decoder
                h_pred, contact_pred = decoder(h_out)
                local_q_v_pred = h_pred[:,:,:target_in]
                local_q_pred = local_q_v_pred + local_q_t

                local_q_pred_ = local_q_pred.view(local_q_pred.size(0), local_q_pred.size(1), -1, 4)
                local_q_pred_ = local_q_pred_ / torch.norm(local_q_pred_, dim = -1, keepdim = True)

                root_v_pred = h_pred[:,:,target_in:]
                root_pred = root_v_pred + root_p_t

                # FK
                root_pred = root_pred.squeeze()
                local_q_pred_ = local_q_pred_.squeeze() # (seq, joint, 4)
                pos_pred = skeleton.forward_kinematics(root_pred, local_q_pred_, rot_repr='quaternion')
                
                
                # Plotting
                start_pose = global_pos[inference_batch_index, 0].numpy()
                in_between_pose = pos_pred[inference_batch_index].numpy()
                in_between_true = global_pos[inference_batch_index, t].numpy()
                target_pose = global_pos[inference_batch_index, -1].numpy()

                plot_pose(start_pose, in_between_pose, target_pose, t, time_stamp, skeleton, pred=True)
                plot_pose(start_pose, in_between_true, target_pose, t, time_stamp, skeleton, pred=False)

                pred_img = Image.open('results/'+ time_stamp +'/tmp/pred_'+str(t)+'.png', 'r')
                gt_img = Image.open('results/'+ time_stamp +'/tmp/gt_'+str(t)+'.png', 'r')

                img_pred.append(pred_img)
                img_gt.append(gt_img)
                img_integrated.append(np.concatenate([pred_img, gt_img.resize(pred_img.size)], 1))
            
            # if i_batch < 49:
            gif_path = os.path.join(result_gif_path, 'img_%02d.gif' % i_batch)
            imageio.mimsave(gif_path, img_integrated, duration=0.1)


if __name__ == '__main__':
    test()
