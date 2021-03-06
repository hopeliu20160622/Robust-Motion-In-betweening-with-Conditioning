from torch.utils.data import Dataset
from rmi.lafan1 import extract, utils
import numpy as np
import torch


class LAFAN1Dataset(Dataset):
    def __init__(self, lafan_path: str, train: bool, device: str):
        self.lafan_path = lafan_path

        self.train = train

        # 4.3: It contains actions performedby 5 subjects, with Subject 5 used as the test set.
        self.actors = (
            ["subject1", "subject2", "subject3", "subject4"] if train else ["subject5"]
        )

        # 4.3: ... The training statistics for normalization are computed on windows of 50 frames offset by 20 frames.
        self.window = 50

        # 4.3: Given the larger size of ... we sample our test windows from Subject 5 at every 40 frames.
        # The training statistics for normalization are computed on windows of 50 frames offset by 20 frames.
        self.offset = 10 if self.train else 40

        # TODO: 3.7.1 Progressive grpowing of transitions. (Curriculum learning strategy)
        self.cur_seq_length = 50

        self.device = device

        self.data = self.load_lafan()  # Call this last

    @property
    def root_v_dim(self):
        return self.data["root_v"].shape[2]

    @property
    def local_q_dim(self):
        return self.data["local_q"].shape[2] * self.data["local_q"].shape[3]

    @property
    def contact_dim(self):
        return self.data["contact"].shape[2]

    @property
    def num_joints(self):
        return self.data["global_pos"].shape[2]

    def load_lafan(self):
        # This uses method provided with LAFAN1.
        # X and Q are local position/quaternion. Motions are rotated to make 10th frame facing X+ position.
        # Refer to paper 3.1 Data formatting
        X, Q, parents, contacts_l, contacts_r, seq_names = extract.get_lafan1_set(
            self.lafan_path, self.actors, self.window, self.offset
        )

        # Retrieve global representations. (global quaternion, global positions)
        _, global_pos = utils.quat_fk(Q, X, parents)

        # Extract std to scale position (refer to: 3.7.3: we scale all our losses...)
        self.global_pos_std = torch.Tensor(global_pos.std(axis=(0, 1))).to(self.device)

        input_data = {}
        input_data["local_q"] = Q  # q_{t}
        input_data["local_q_offset"] = Q[:, -1, :, :]  # lasst frame's quaternions
        input_data["q_target"] = Q[:, -1, :, :]  # q_{T}

        input_data["root_v"] = (
            global_pos[:, 1:, 0, :] - global_pos[:, :-1, 0, :]
        )  # \dot{r}_{t}
        input_data["root_p_offset"] = global_pos[
            :, -1, 0, :
        ]  # last frame's root positions
        input_data["root_p"] = global_pos[:, :, 0, :]

        input_data["contact"] = np.concatenate(
            [contacts_l, contacts_r], -1
        )  # Foot contact
        input_data["global_pos"] = global_pos[
            :, :, :, :
        ]  # global position (N, 50, 22, 30) why not just global_pos

        # Experimental: If dance: [1, 0], jump: [0, 1], else: [0, 0]
        motion_condition = np.zeros((len(seq_names), self.cur_seq_length, 2))
        for ind, seq_name in enumerate(seq_names):
            if "dance" in seq_name:
                motion_condition[ind, :, 0] = 1
            elif "jump" in seq_name:
                motion_condition[ind, :, 1] = 1
            else:
                continue
        input_data["seq_names"] = motion_condition
        
        return input_data

    def __len__(self):
        return self.data["global_pos"].shape[0]

    def __getitem__(self, index):
        query = {}
        query["local_q"] = self.data["local_q"][index].astype(np.float32)
        query["local_q_offset"] = self.data["local_q_offset"][index].astype(np.float32)
        query["q_target"] = self.data["q_target"][index].astype(np.float32)
        query["root_v"] = self.data["root_v"][index].astype(np.float32)
        query["root_p_offset"] = self.data["root_p_offset"][index].astype(np.float32)
        query["root_p"] = self.data["root_p"][index].astype(np.float32)
        query["contact"] = self.data["contact"][index].astype(np.float32)
        query["global_pos"] = self.data["global_pos"][index].astype(np.float32)
        query["seq_names"] = self.data["seq_names"][index].astype(np.float32)
        return query
