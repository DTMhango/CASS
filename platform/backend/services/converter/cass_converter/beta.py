"""The beta distribution, to the precision a damage-bin discretisation needs.

GEM publishes each vulnerability function as a mean loss ratio and a
coefficient of variation at every intensity level, with ``dist="BT"`` -- the
loss ratio at a given intensity is beta distributed. Oasis wants something
different: the probability mass in each damage bin. Getting from one to the
other needs the beta cumulative distribution function, evaluated a few million
times per country.

This is written out rather than taken from SciPy for one reason worth stating.
The keys and converter services have no third-party numerical dependency, and
adding one to a control plane so that a model build can call a CDF would put
the whole of SciPy's release cadence on the critical path of a regulated
calculation. The regularised incomplete beta function is forty lines of
standard numerical analysis, its algorithm is published and stable, and it can
be tested against a closed form -- which is what ``tests/test_beta.py`` does,
rather than against another implementation's output.

The algorithm is the continued-fraction evaluation of the regularised
incomplete beta function from Numerical Recipes (Press et al., 3rd edition,
section 6.4), using the symmetry relation to keep the fraction in its rapidly
converging range. ``math.lgamma`` supplies the log beta function.
"""

from __future__ import annotations

import math

#: Relative accuracy the continued fraction is iterated to. Well beyond what a
#: damage bin can express -- the point is that the discretisation error is the
#: bin width, not the arithmetic.
EPSILON = 1e-14

#: Guard against division by zero in Lentz's method.
TINY = 1e-300

#: Iterations before the fraction is declared not to converge. It converges in
#: well under a hundred for every argument in range; this is a tripwire, not a
#: tuning parameter.
MAX_ITERATIONS = 300


class BetaError(Exception):
    """Raised when a beta distribution cannot be formed or evaluated."""


def log_beta(a: float, b: float) -> float:
    """Log of the beta function B(a, b), via log gamma."""
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _continued_fraction(a: float, b: float, x: float) -> float:
    """The modified Lentz evaluation of the incomplete beta continued fraction."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0

    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < TINY:
        d = TINY
    d = 1.0 / d
    result = d

    for m in range(1, MAX_ITERATIONS + 1):
        m2 = 2 * m

        # The even step.
        numerator = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + numerator * d
        if abs(d) < TINY:
            d = TINY
        c = 1.0 + numerator / c
        if abs(c) < TINY:
            c = TINY
        d = 1.0 / d
        result *= d * c

        # The odd step.
        numerator = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + numerator * d
        if abs(d) < TINY:
            d = TINY
        c = 1.0 + numerator / c
        if abs(c) < TINY:
            c = TINY
        d = 1.0 / d
        step = d * c
        result *= step

        if abs(step - 1.0) < EPSILON:
            return result

    raise BetaError(
        f"The incomplete beta continued fraction did not converge for "
        f"a={a!r}, b={b!r}, x={x!r} within {MAX_ITERATIONS} iterations."
    )


def regularised_incomplete_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b): the beta CDF at ``x`` for shape parameters ``a`` and ``b``."""
    if a <= 0 or b <= 0:
        raise BetaError(
            f"Beta shape parameters must be positive; received a={a!r}, b={b!r}."
        )
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    front = math.exp(
        a * math.log(x) + b * math.log1p(-x) - log_beta(a, b)
    )

    # The fraction converges quickly only for x below the distribution's
    # centre of mass. Above it, evaluate the mirrored problem and subtract.
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _continued_fraction(a, b, x) / a
    return 1.0 - front * _continued_fraction(b, a, 1.0 - x) / b


def shape_parameters(mean: float, cov: float) -> tuple[float, float]:
    """The beta shape parameters for a mean loss ratio and its CoV.

    GEM states a mean and a coefficient of variation; the beta distribution is
    parameterised by two shapes. The relation is standard, and so is its
    constraint: a beta variable on [0, 1] with mean m cannot have a variance
    at or above m(1 - m), which is the variance of the two-point distribution
    on the ends. A CoV that implies more spread than that does not describe a
    beta distribution at all.

    That constraint is enforced rather than clamped. Clamping would silently
    substitute a different distribution for the one the source published, and
    the loss it produced would be attributed to GEM.
    """
    if not 0.0 < mean < 1.0:
        raise BetaError(
            f"A beta loss ratio needs a mean strictly between 0 and 1; got {mean!r}."
        )
    if cov <= 0.0:
        raise BetaError(f"A beta loss ratio needs a positive CoV; got {cov!r}.")

    variance = (cov * mean) ** 2
    ceiling = mean * (1.0 - mean)
    if variance >= ceiling:
        raise BetaError(
            f"A mean loss ratio of {mean!r} with a CoV of {cov!r} implies a variance "
            f"of {variance:.6g}, which is at or above the maximum {ceiling:.6g} any "
            "distribution on [0, 1] with that mean can have. This is not a beta "
            "distribution, and it is not something to clamp: the source function "
            "says something the chosen family cannot express."
        )

    common = ceiling / variance - 1.0
    return mean * common, (1.0 - mean) * common


def cdf(mean: float, cov: float, x: float) -> float:
    """The probability that a beta loss ratio of this mean and CoV is at most x."""
    a, b = shape_parameters(mean, cov)
    return regularised_incomplete_beta(a, b, x)


def partial_expectation(mean: float, cov: float, lower: float, upper: float) -> float:
    """E[X . 1{lower < X <= upper}] for a beta loss ratio.

    The contribution one damage bin makes to the mean, which is what a damage
    bin actually has to carry. It follows from the standard identity

        E[X . 1{X <= x}] = a / (a + b) . I_x(a + 1, b)

    -- shifting the first shape parameter by one turns the CDF into the
    integral of x against the density. Having it in closed form is what makes
    the mean-preserving placement in ``vulnerability`` exact rather than a
    numerical quadrature with its own error to argue about.
    """
    if upper <= lower:
        return 0.0
    a, b = shape_parameters(mean, cov)
    scale = a / (a + b)
    return scale * (
        regularised_incomplete_beta(a + 1.0, b, min(upper, 1.0))
        - regularised_incomplete_beta(a + 1.0, b, max(lower, 0.0))
    )
