import torch
import torch.nn as nn
from rmi.model.plu import PLU


class InputEncoder(nn.Module):
    def __init__(self, input_dim=128, hidden_dim=512, out_dim=256):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.out_dim = out_dim

        self.fc1 = nn.Linear(input_dim, hidden_dim, bias=True)
        self.fc2 = nn.Linear(hidden_dim, out_dim, bias=True)

    def forward(self, x):
        x = self.fc1(x)
        x = PLU(x)
        x = self.fc2(x)
        x = PLU(x)
        return x


class LSTMNetwork(nn.Module):
    def __init__(self, input_dim=128, hidden_dim=256 * 3, num_layer=1, device="cpu"):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layer = num_layer
        self.lstm = nn.LSTM(self.input_dim, self.hidden_dim, self.num_layer)

        self.device = device

    def init_hidden(self, batch_size):
        self.h = torch.zeros(
            (self.num_layer, batch_size, self.hidden_dim), device=self.device
        )
        self.c = torch.zeros(
            (self.num_layer, batch_size, self.hidden_dim), device=self.device
        )

    def forward(self, x):
        x, (self.h, self.c) = self.lstm(x, (self.h, self.c))
        return x


class Decoder(nn.Module):
    def __init__(self, input_dim=128, hidden_dim=512, out_dim=256):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.out_dim = out_dim

        self.fc1 = nn.Linear(input_dim, hidden_dim, bias=True)
        self.fc2 = nn.Linear(hidden_dim, hidden_dim // 2, bias=True)
        self.fc3 = nn.Linear(hidden_dim // 2, out_dim - 4, bias=True)
        self.fc_contact = nn.Linear(hidden_dim // 2, 4, bias=True)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        x = self.fc1(x)
        x = PLU(x)
        x = self.fc2(x)
        x = PLU(x)
        hidden_out = self.fc3(x)
        contact = self.fc_contact(x)
        contact_out = self.sigmoid(contact)
        return hidden_out, contact_out


class Discriminator(nn.Module):
    # refer: 3.5 Motion discriminators, 3.7.2 sliding critics
    def __init__(self, input_dim=128, hidden_dim=512, out_dim=1, length=3):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.out_dim = out_dim
        self.length = length

        self.fc1 = nn.Conv1d(
            self.input_dim, self.hidden_dim, kernel_size=self.length, bias=True
        )
        self.fc2 = nn.Conv1d(
            self.hidden_dim, self.hidden_dim // 2, kernel_size=1, bias=True
        )
        self.fc3 = nn.Conv1d(self.hidden_dim // 2, out_dim, kernel_size=1, bias=True)

        self.relu = nn.ReLU()

    def forward(self, x):
        x = self.fc1(x)
        x = self.relu(x)
        x = self.fc2(x)
        x = self.relu(x)
        x = self.fc3(x)
        return x
