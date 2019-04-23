import torch
import numpy as np
from .flows import Flow
from .resnet import ResNet


class AffineCouplingLayer(Flow):
    r"""
    Affine coupling layer

    .. math::
        :nowrap:

        \begin{eqnarray*}
        \mathbf{y}_{1:d} &=& \mathbf{x}_{1:d} \\
        \mathbf{y}_{d+1:D} &=& \mathbf{x}_{d+1:D} \odot \exp(s(\mathbf{x}_{1:d})+t(\mathbf{x}_{1:d}))
        \end{eqnarray*}

    """

    def __init__(self, in_channels, mask_type="channel_wise",
                 scale_net=None, translate_net=None, scale_translate_net=None,
                 inverse_mask=False):
        super().__init__(in_channels)

        # mask initializations
        if mask_type in ["checkerboard", "channel_wise"]:
            self.mask_type = mask_type
        else:
            raise ValueError

        self.inverse_mask = inverse_mask

        self.scale_net = None
        self.translate_net = None
        self.scale_translate_net = None

        if scale_net and translate_net:
            self.scale_net = scale_net
            self.translate_net = translate_net
        else:
            if scale_translate_net:
                self.scale_translate_net = scale_translate_net
            else:
                # Set a default network.
                self.scale_translate_net =\
                    ResNet(in_channels, 64, 2 * in_channels,
                           num_blocks=8, kernel_size=3, padding=1,
                           double_after_norm=(self.mask_type == "checkerboard"))

    def build_mask(self, x):
        """
        Parameters
        ----------
        x : torch.Tensor

        Returns
        -------
        mask : torch.tensor

        Examples
        --------
        >>> f1 = AffineCouplingLayer(4, mask_type="channel_wise", inverse_mask=False)
        >>> x1 = torch.randn([1,4,3,3])
        >>> f1.build_mask(x1)
        tensor([[[[1.]],
        <BLANKLINE>
                 [[1.]],
        <BLANKLINE>
                 [[0.]],
        <BLANKLINE>
                 [[0.]]]])
        >>> f2 = AffineCouplingLayer(2, mask_type="checkerboard", inverse_mask=True)
        >>> x2 = torch.randn([1,2,5,5])
        >>> f2.build_mask(x2)
        tensor([[[[0., 1., 0., 1., 0.],
                  [1., 0., 1., 0., 1.],
                  [0., 1., 0., 1., 0.],
                  [1., 0., 1., 0., 1.],
                  [0., 1., 0., 1., 0.]]]])

        """
        [_, channels, height, width] = x.shape
        if self.mask_type == "checkerboard":
            mask = checkerboard_mask(height, width, self.inverse_mask)
            return torch.from_numpy(mask).view(1, 1, height, width).to(x.device)
        else:
            mask = channel_wise_mask(channels, self.inverse_mask)
            return torch.from_numpy(mask).view(1, channels, 1, 1).to(x.device)

    def get_parameters(self, x):
        """
        Parameters
        ----------
        x : torch.tensor

        Returns
        -------
        s : torch.tensor
        t : torch.tensor
        x_masked : tensor.torch
        x_inverse_masked : tensor.torch

        Examples
        --------
        >>> # Channel-wise mask
        >>> f1 = AffineCouplingLayer(4, mask_type="channel_wise", inverse_mask=False)
        >>> x1 = torch.randn([1,4,3,3])
        >>> s, t, x_masked, x_inverse_masked = f1.get_parameters(x1)
        >>> print(torch.sum(s[:, :2, :, :]).data)
        tensor(0.)
        >>> print(torch.sum(x_masked[:, 2:, :, :]).data)
        tensor(0.)
        >>> # Checkerboard mask
        >>> f2 = AffineCouplingLayer(2, mask_type="checkerboard", inverse_mask=True)
        >>> x2 = torch.randn([1,2,5,5])
        >>> s, t, x_masked, x_inverse_masked = f2.get_parameters(x2)
        >>> print(torch.sum(x_masked[:,:,::2, ::2]).data)
        tensor(0.)
        >>> print(torch.sum(x_masked[:,:,1::2, 1::2]).data)
        tensor(0.)

        """
        mask = self.build_mask(x)
        x_masked = mask * x
        x_inverse_masked = (1 - mask) * x

        if self.scale_translate_net:
            s_t = self.scale_translate_net(x_masked)
            s, t = s_t.chunk(2, dim=1)
        else:
            s = self.scale_net(x_masked)
            t = self.translate_net(x_masked)

        s = s * (1 - mask)
        t = t * (1 - mask)

        return s, t, x_masked, x_inverse_masked

    def forward(self, x, compute_jacobian=True):
        s, t, x_masked, x_reverse_masked = self.get_parameters(x)
        z = x_masked + (x_reverse_masked * torch.exp(s) + t)
        if compute_jacobian:
            self._logdet_jacobian = s.view(s.shape(0), -1).sum(-1)

        return z

    def inverse(self, z):
        s, t, z_masked, z_reverse_masked = self.get_parameters(z)
        x = z_masked + (z_reverse_masked - t) * torch.exp(-s)

        return x

    def extra_repr(self):
        return 'in_features={}, mask_type={}, inverse_mask={}'.format(
            self.in_features, self.mask_type, self.inverse_mask
        )


def checkerboard_mask(height, width, inverse_mask=False):
    """
    Parameters
    ----------
    height : int
    width : int
    inverse_mask : bool

    Returns
    -------
    mask : np.array

    Examples
    --------
    >>> checkerboard_mask(5, 4, False)
    array([[1., 0., 1., 0.],
           [0., 1., 0., 1.],
           [1., 0., 1., 0.],
           [0., 1., 0., 1.],
           [1., 0., 1., 0.]], dtype=float32)
    >>> checkerboard_mask(5, 4, True)
    array([[0., 1., 0., 1.],
           [1., 0., 1., 0.],
           [0., 1., 0., 1.],
           [1., 0., 1., 0.],
           [0., 1., 0., 1.]], dtype=float32)

    """
    mask = np.arange(height).reshape(-1, 1) + np.arange(width)
    mask = np.mod((inverse_mask is False) + mask, 2)

    return mask.astype(np.float32)


def channel_wise_mask(channels, inverse_mask=False):
    """
    Parameters
    ----------
    channels : int
    inverse_mask : bool

    Returns
    -------
    mask : np.array

    Examples
    --------
    >>> channel_wise_mask(6, False)
    array([1., 1., 1., 0., 0., 0.], dtype=float32)
    >>> channel_wise_mask(6, True)
    array([0., 0., 0., 1., 1., 1.], dtype=float32)

    """
    mask = np.zeros(channels).astype(np.float32)
    if inverse_mask:
        mask[channels // 2:] = 1
    else:
        mask[:channels // 2] = 1

    return mask