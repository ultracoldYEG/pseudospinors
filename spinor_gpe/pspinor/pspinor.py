# -*- coding: utf-8 -*-
"""
Created on Wed Apr  7 12:38:56 2021

@author: benjamin
"""

# Classes & Modules:
#  - Spinors
#  - PropResult
#  - TensorPropagator
#  - tensor_tools
#  - constants

import numpy as np
# from matplotlib import pyplot as plt
from spinor_gpe import constants as const


class PSpinor:
    """A GPU-compatible simulator of the pseudospin-1/2 GPE.

    Contains the functionality to run a real- or imaginary-time propataion of
    the pseudospin-1/2 Gross-Pitaevskii equation. Contains methods to generate
    the required energy and spatial grids. Also has methods to generate the
    grids for the momentum-(in)dependent coupling between spin components,
    corresponding to an (RF) Raman coupling interaction.

    """

    # pylint: disable=too-many-instance-attributes
    def __init__(self,
                 atom_num=1e4, pop_frac=(0.5, 0.5), phase_factor=1, omeg=None,
                 g_sc=None, is_coupling=False, mesh_points=(256, 256),
                 r_sizes=(16, 16)):
        """Instantiate a Spinor object.

        Generates the parameters and
        basic energy grids required for propagation.

        Parameters
        ----------
        atom_num : int, optional
            Total atom number.
        pop_frac : :obj:`array_like` of :obj:`float`, optional
            Starting population fraction in each spin component.
        phase_factor : :obj:`complex`
            Unit complex number; initial relative phase factor between the two
            spin components.
        omeg : :obj:`dict`, optional
            Trapping frequencies, {x, y, z} [rad/s].
        g_sc : :obj:`dict`, optional
            Relative coupling strengths for scattering interactions,
            {uu, dd, ud}. Intercomponent interaction are assummed to be
            symmetric, i.e. ud == du.
        is_coupling : :obj:`bool`, optional
            Momentum-(in)dependent coupling between spin components.
        mesh_points : :obj:`iterable` of :obj:`int`, optional
            The number of grid points along the x- and y-axes, respectively.
        r_sizes : :obj:`iterable` of :obj:`int`, optional
            The half size of the real space grid along the x- and y-axes,
            respectively, in units of [a_x].
        """
        # pylint: disable=too-many-arguments
        self.atom_num = atom_num

        assert sum(pop_frac) == 1.0, """Total population must equal 1"""
        self.pop_frac = pop_frac  #: Spins' initial population fraction

        if omeg is None:
            omeg0 = 2*np.pi*50
            #: dict: Angular trapping frequencies
            self.omeg = {'x': omeg0, 'y': omeg0, 'z': 40 * omeg0}
        else:
            omeg_names = {'x', 'y', 'z'}
            assert omeg_names == omeg.keys(), f"""Keys for `omeg` must have
                the form: {omeg_names}"""
            self.omeg = omeg

        if g_sc is None:
            #: dict: Relative scattering interaction strengths
            self.g_sc = {'uu': 1.0, 'dd': 0.995, 'ud': 0.995}
        else:
            g_names = {'uu', 'dd', 'ud'}
            assert g_names == g_sc.keys(), f"""Keys for `g_sc` must have
                the form: {g_names}"""
            self.g_sc = g_sc

        self.is_coupling = is_coupling  #: Presence of spin coupling

        self.compute_tf_params()
        self.compute_spatial_grids(mesh_points, r_sizes)
        self.compute_energy_grids()
        self.compute_tf_psi(phase_factor)

        self.prop = None
        self.n_steps = None
        # TODO: Implement file directory management for the simulation.

    def compute_tf_psi(self, phase_factor):
        """Compute the intial pseudospinor wavefunction psi.

        `psi` is a list of 2D numpy arrays.
        """
        assert abs(phase_factor) == 1.0, """Relative phase factor must have
            unit magnitude."""
        g_bare = [self.g_sc['uu'], self.g_sc['dd']]
        # self.psi = [np.sqrt(self.atom_num * pop / ) for pop, g in zip(self.pop_frac, self.g_sc)]

    def compute_tf_params(self):
        """Compute parameters and scales for the Thomas-Fermi solution."""
        #: Relative size of y-axis trapping frequency relative to x-axis.
        self.y_trap = self.omeg['y'] / self.omeg['x']
        #: Relative size of z-axis trapping frequency relative to x-axis.
        self.z_trap = self.omeg['z'] / self.omeg['x']
        #: float: Harmonic oscillator length scale [m].
        self.a_x = np.sqrt(const.hbar / (const.Rb87['m'] * self.omeg['x']))

        #: Dimensionless scattering length, [a_x]
        self.a_sc = const.Rb87['a_sc'] / self.a_x
        #: Chemical potential for an asymmetric harmonic BEC, [hbar*omeg_x].
        self.chem_pot = ((4 * self.atom_num * self.a_sc * self.y_trap
                          * np.sqrt(self.z_trap / (2 * np.pi)))**(1/2))
        self.rad_tf = np.sqrt(2 * self.chem_pot)  #: Thomas-Fermi radius [a_x].

        self.e_scale = 1  #: Energy scale [hbar*omeg_x]
        self.r_scale = 1  #: Length scale [a_x]
        self.time_scale = 1 / self.omeg['x']  #: Time scale [1/omeg_x]

    def compute_spatial_grids(self, mesh_points, r_sizes):
        """Compute the real and momentum space grids.

        Parameters
        ----------
        mesh_points : :obj:`list` of :obj:`int`
            The number of grid points along the x- and y-axes, respectively.
        r_sizes : :obj:`list` of :obj:`int`, optional
            The half size of the grid along the real x- and y-axes,
            respectively,in units of [a_x].
        """
        assert all(point % 2 == 0 for point in mesh_points), f"""Number of
            mesh points {mesh_points} should be powers of 2"""
        self.mesh_points = np.array(mesh_points)
        self.r_sizes = np.array(r_sizes)

        #: Spacing between real-space mesh points [a_x]
        self.delta_r = 2 * self.r_sizes / self.mesh_points
        #: Half size of the grid along the kx- and ky- axes [1/a_x]
        self.k_sizes = np.pi / self.delta_r
        #: Spacing between momentum-space mesh points [1/a_x]
        self.delta_k = np.pi / self.r_sizes

        #: Linear arrays for real- [a_x] and k-space [1/a_x], x- and y-axes
        self.x_lin = self._compute_lin(self.r_sizes, self.mesh_points, axis=0)
        self.y_lin = self._compute_lin(self.r_sizes, self.mesh_points, axis=1)
        self.kx_lin = self._compute_lin(self.r_sizes, self.mesh_points, axis=0)
        self.ky_lin = self._compute_lin(self.r_sizes, self.mesh_points, axis=1)

        #: 2D meshes for computing the energy grids [a_x] and [1/a_x]
        self.x_mesh, self.y_mesh = np.meshgrid(self.x_lin, self.y_lin)
        self.kx_mesh, self.ky_mesh = np.meshgrid(self.x_lin, self.y_lin)

        # ??? Add functionality for Tukey filter window?

        #: Real-space volume element used for normalization [a_x^2]
        self.dv_r = np.prod(self.delta_r)
        #: k-space volume element used for normalization [1/a_x^2]
        self.dv_k = np.prod(self.delta_k)

    @classmethod
    def _compute_lin(cls, sizes, points, axis=0):
        """Compute linear 1D arrays of real or momentum space mesh points.

        Parameters
        ----------
        sizes : array
            The half sizes of the mesh
        points : array
            The number of points in the mesh
        axis : :obj:`int`, optional
            The axis along which to generate: 0 -> 'x'; 1 -> 'y'
        """
        return np.linspace(-sizes[axis], sizes[axis], num=points[axis],
                           endpoint=False)

    def compute_energy_grids(self):
        """Compute basic potential and kinetic energy grids.

        Assumes that the BEC is in a harmonic trap. This harmonic potential
        determines the initial 'Thomas-Fermi' density profile of the BEC.
        `pot_eng` can be modified prior to progation to have any arbitrary
        potential energy landscape.

        Assumes that the BEC has a simple free-particle kinetic energy
        dispersion. If using a momentum-dependent spin coupling, this grid
        will be modified later.
        """
        #: Potential energy grid [hbar*omeg_x]
        self.pot_eng = (self.x_mesh**2 + (self.y_trap * self.y_mesh)**2) / 2
        #: Kinetic energy grid [hbar*omeg_x]
        self.kin_eng = (self.kx_mesh**2 + self.ky_mesh**2) / 2

    def imaginary(self):
        """Perform imaginary-time propagation."""
        self.prop = TensorPropagator(self)
        return PropResult()

    def real(self):
        """Perform real-time propagation."""
        self.prop = TensorPropagator(self)
        return PropResult()

    def coupling_setup(self, **kwargs):
        """Calculate parameters for the momentum-(in)dependent coupling."""
        # pass wavelength, relative scaling of k_L, momentum-(in)depenedency

    def omega_grad(self):
        """Generate linear gradient of the interspin coupling strength."""

    def omega_uniform(self):
        """Generate a uniform interspin coupling strength."""

    def detuning_grad(self):
        """Generate a linear gradient of the coupling detuning."""

    def detuning_uniform(self):
        """Generate a uniform coupling detuning."""


class PropResult:
    """Results of propagation, along with plotting and analysis tools."""

    def __init__(self):
        pass

    def plot_spins(self):
        """Plot the real- and momentum-space densities of the spinor
        wavefunction, along with the phase of real=space wavefunction.
        """

    def plot_total(self):
        """Plot the total real-space density and phase of the wavefunction."""

    def plot_eng(self):
        """Plot the sampled energy expectation values."""

    def plot_pops(self):
        """Plot the spin populations as a function of propagation time."""

    def analyze_vortex(self):
        """Compute the total vorticity in each spin component."""

    def make_movie(self):
        """Generate a movie of the wavefunctions' densities and phases."""


class TensorPropagator:
    """Propagator of the GPE using tensor; computed on either the CPU or
    the GPU.
    """

    # Object that sucks in the needed energy grids and parameters for
    # propagation, converts them to tensors, & performs the propagation.
    #  - It means that two copies of the grids aren't carried in the main class
    #  - However, it restricts access to the tensor form of the grids; unless
    #    I keep the Propagation object as a class "attribute".
    #  - I can directly pass `self` to this class and access class attributes,
    #    methods, esp. energy grids. Only do this in the __init__ function
    #    so as to not store the main Spinor object in the class

    # --> Should it be a class or just a pure function??
    #      - Maybe a class because then it can store the grids it needs, and
    #        then access them from the different functions for free.
    #      - It would allow these operations to reside in a separate module.
    # BUT, then I have two classes who are attributes of each other, and
    #     THAT's a weird structure.
    #    - Maybe this class doesn't have to attribute the other one; it just
    #      sucks in the data it needs.

    # Will need to calculate certain data throughout the loop, and then
    #     create and populate the PropResult object.

    def __init__(self, spin):
        # Needs:
        #  - Energy grids
        #  - Raman grids
        #  - Atom number
        #  - Number of steps
        #  - grid parameters [don't keep tensor versions in a dict, not stable]
        #  - dt
        #  - sample (bool)
        #  - wavefunction sample frequency
        #  - wavefunction anneal frequency (imaginary time)
        #  - device (cpu vs. gpu)
        #  - volume elements

        print(spin.n_steps)

    def evolution_op(self):
        """Compute the time-evolution operator for a given energy term."""

    def coupling_op(self):
        """Compute the time-evolution operator for the coupling term."""

    def single_step(self):
        """Single step forward in real or imaginary time."""

    def full_step(self):
        """ Divide the full propagation step into three single steps using
        the magic gamma for accuracy.
        """
        self.single_step()
        self.single_step()
        self.single_step()

    def propagation(self, n_steps):
        """Contains the actual propagation for-loop."""
        for _i in range(n_steps):
            self.full_step()

    def energy_exp(self):
        """Compute the energy expectation value."""

    def normalize(self):
        """Normalize the wavefunction to the expected atom number."""

    def density(self):
        """Compute the density of the given wavefunction."""

    def inner_prod(self):
        """Compute the inner product of two wavefunctions."""

    def expect_val(self):
        """Compute the expectation value of the supplied spatial operator."""


#  ----------------- tensor_tools MODULE ---------------
# Would it be a good idea to allow all these functions to accept both arrays
# and tensors? Maybe, for completeness it's a good idea.

def fft_1d():
    """Take a list of tensors or np arrays; checks type."""


def fft_2d():
    """Take a list of tensors or np arrays; checks type."""


def fftshift():
    """Shift the zero-frequency component to the center of the spectrum."""


def ifftshift():
    """Inverse of `fftshift`."""


def to_numpy():
    """Convert from tensors to numpy arrays."""


def to_tensor():
    """Convert from numpy arrays to tensors."""


def t_mult(first, second):
    """Assert that a and b are tensors."""
    return first * second


def norm_sq():
    """Take a list of tensors or np arrays; checks type."""


def t_cosh():
    """Hyperbolic cosine of a complex tensor."""


def t_sinh():
    """Hyperbolic sine of a complex tensor."""


def grad():
    """Take a list of tensors or np arrays; checks type."""


def grad__sq():
    """Take a list of tensors or np arrays; checks type."""


def conj():
    """Complex conjugate of a complex tensor."""


# ----- DOCUMENTATION -----
#  - `sphinx`
#  - `sphinx.ext.autodoc`; this website was helpful:
#  - `sphinx.ext.napoleon` --> for using NumPy documentation style;
#    alternatively, use `numpydoc`; here is their style guide:
#    https://numpydoc.readthedocs.io/en/latest/format.html
#  - ReadTheDocs, for hosting the documentation once it's good
