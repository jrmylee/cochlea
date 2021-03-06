# from gansynth
import numpy as np
import tensorflow.compat.v1 as tf

tf.disable_v2_behavior()

# mel spectrum constants.
_MEL_BREAK_FREQUENCY_HERTZ = 700.0
_MEL_HIGH_FREQUENCY_Q = 1127.0


def mel_to_hertz(mel_values):
  """Converts frequencies in `mel_values` from the mel scale to linear scale."""
  return _MEL_BREAK_FREQUENCY_HERTZ * (
      np.exp(np.array(mel_values) / _MEL_HIGH_FREQUENCY_Q) - 1.0)


def hertz_to_mel(frequencies_hertz):
  """Converts frequencies in `frequencies_hertz` in Hertz to the mel scale."""
  return _MEL_HIGH_FREQUENCY_Q * np.log(
      1.0 + (np.array(frequencies_hertz) / _MEL_BREAK_FREQUENCY_HERTZ))


def linear_to_mel_weight_matrix(num_mel_bins=20,
                                num_spectrogram_bins=129,
                                sample_rate=16000,
                                lower_edge_hertz=125.0,
                                upper_edge_hertz=3800.0):
  """Returns a matrix to warp linear scale spectrograms to the mel scale.
  Adapted from tf.signal.linear_to_mel_weight_matrix with a minimum
  band width (in Hz scale) of 1.5 * freq_bin. To preserve accuracy,
  we compute the matrix at float64 precision and then cast to `dtype`
  at the end. This function can be constant folded by graph optimization
  since there are no Tensor inputs.
  Args:
    num_mel_bins: Int, number of output frequency dimensions.
    num_spectrogram_bins: Int, number of input frequency dimensions.
    sample_rate: Int, sample rate of the audio.
    lower_edge_hertz: Float, lowest frequency to consider.
    upper_edge_hertz: Float, highest frequency to consider.
  Returns:
    Numpy float32 matrix of shape [num_spectrogram_bins, num_mel_bins].
  Raises:
    ValueError: Input argument in the wrong range.
  """
  # Validate input arguments
  if num_mel_bins <= 0:
    raise ValueError('num_mel_bins must be positive. Got: %s' % num_mel_bins)
  if num_spectrogram_bins <= 0:
    raise ValueError(
        'num_spectrogram_bins must be positive. Got: %s' % num_spectrogram_bins)
  if sample_rate <= 0.0:
    raise ValueError('sample_rate must be positive. Got: %s' % sample_rate)
  if lower_edge_hertz < 0.0:
    raise ValueError(
        'lower_edge_hertz must be non-negative. Got: %s' % lower_edge_hertz)
  if lower_edge_hertz >= upper_edge_hertz:
    raise ValueError('lower_edge_hertz %.1f >= upper_edge_hertz %.1f' %
                     (lower_edge_hertz, upper_edge_hertz))
  if upper_edge_hertz > sample_rate / 2:
    raise ValueError('upper_edge_hertz must not be larger than the Nyquist '
                     'frequency (sample_rate / 2). Got: %s for sample_rate: %s'
                     % (upper_edge_hertz, sample_rate))

  # HTK excludes the spectrogram DC bin.
  bands_to_zero = 1
  nyquist_hertz = sample_rate / 2.0
  linear_frequencies = np.linspace(
      0.0, nyquist_hertz, num_spectrogram_bins)[bands_to_zero:, np.newaxis]
  # spectrogram_bins_mel = hertz_to_mel(linear_frequencies)

  # Compute num_mel_bins triples of (lower_edge, center, upper_edge). The
  # center of each band is the lower and upper edge of the adjacent bands.
  # Accordingly, we divide [lower_edge_hertz, upper_edge_hertz] into
  # num_mel_bins + 2 pieces.
  band_edges_mel = np.linspace(
      hertz_to_mel(lower_edge_hertz), hertz_to_mel(upper_edge_hertz),
      num_mel_bins + 2)

  lower_edge_mel = band_edges_mel[0:-2]
  center_mel = band_edges_mel[1:-1]
  upper_edge_mel = band_edges_mel[2:]

  freq_res = nyquist_hertz / float(num_spectrogram_bins)
  freq_th = 1.5 * freq_res
  for i in range(0, num_mel_bins):
    center_hz = mel_to_hertz(center_mel[i])
    lower_hz = mel_to_hertz(lower_edge_mel[i])
    upper_hz = mel_to_hertz(upper_edge_mel[i])
    if upper_hz - lower_hz < freq_th:
      rhs = 0.5 * freq_th / (center_hz + _MEL_BREAK_FREQUENCY_HERTZ)
      dm = _MEL_HIGH_FREQUENCY_Q * np.log(rhs + np.sqrt(1.0 + rhs**2))
      lower_edge_mel[i] = center_mel[i] - dm
      upper_edge_mel[i] = center_mel[i] + dm

  lower_edge_hz = mel_to_hertz(lower_edge_mel)[np.newaxis, :]
  center_hz = mel_to_hertz(center_mel)[np.newaxis, :]
  upper_edge_hz = mel_to_hertz(upper_edge_mel)[np.newaxis, :]

  # Calculate lower and upper slopes for every spectrogram bin.
  # Line segments are linear in the mel domain, not Hertz.
  lower_slopes = (linear_frequencies - lower_edge_hz) / (
      center_hz - lower_edge_hz)
  upper_slopes = (upper_edge_hz - linear_frequencies) / (
      upper_edge_hz - center_hz)

  # Intersect the line segments with each other and zero.
  mel_weights_matrix = np.maximum(0.0, np.minimum(lower_slopes, upper_slopes))

  # Re-add the zeroed lower bins we sliced out above.
  # [freq, mel]
  mel_weights_matrix = np.pad(mel_weights_matrix, [[bands_to_zero, 0], [0, 0]],
                              'constant')
  return mel_weights_matrix


def diff(x, axis=-1):
  """Take the finite difference of a tensor along an axis.
  Args:
    x: Input tensor of any dimension.
    axis: Axis on which to take the finite difference.
  Returns:
    d: Tensor with size less than x by 1 along the difference dimension.
  Raises:
    ValueError: Axis out of range for tensor.
  """
  shape = x.get_shape()
  if axis >= len(shape):
    raise ValueError('Invalid axis index: %d for tensor with only %d axes.' %
                     (axis, len(shape)))

  begin_back = [0 for unused_s in range(len(shape))]
  begin_front = [0 for unused_s in range(len(shape))]
  begin_front[axis] = 1

  size = shape.as_list()
  size[axis] -= 1
  slice_front = tf.slice(x, begin_front, size)
  slice_back = tf.slice(x, begin_back, size)
  d = slice_front - slice_back
  return d


def unwrap(p, discont=np.pi, axis=-1):
  """Unwrap a cyclical phase tensor.
  Args:
    p: Phase tensor.
    discont: Float, size of the cyclic discontinuity.
    axis: Axis of which to unwrap.
  Returns:
    unwrapped: Unwrapped tensor of same size as input.
  """
  dd = diff(p, axis=axis)
  ddmod = tf.mod(dd + np.pi, 2.0 * np.pi) - np.pi
  idx = tf.logical_and(tf.equal(ddmod, -np.pi), tf.greater(dd, 0))
  ddmod = tf.where(idx, tf.ones_like(ddmod) * np.pi, ddmod)
  ph_correct = ddmod - dd
  idx = tf.less(tf.abs(dd), discont)
  ddmod = tf.where(idx, tf.zeros_like(ddmod), dd)
  ph_cumsum = tf.cumsum(ph_correct, axis=axis)

  shape = p.get_shape().as_list()
  shape[axis] = 1
  ph_cumsum = tf.concat([tf.zeros(shape, dtype=p.dtype), ph_cumsum], axis=axis)
  unwrapped = p + ph_cumsum
  return unwrapped


def instantaneous_frequency(phase_angle, time_axis=-2, use_unwrap=True):
  """Transform a fft tensor from phase angle to instantaneous frequency.
  Take the finite difference of the phase. Pad with initial phase to keep the
  tensor the same size.
  Args:
    phase_angle: Tensor of angles in radians. [Batch, Time, Freqs]
    time_axis: Axis over which to unwrap and take finite difference.
    use_unwrap: True preserves original GANSynth behavior, whereas False will
        guard against loss of precision.
  Returns:
    dphase: Instantaneous frequency (derivative of phase). Same size as input.
  """
  if use_unwrap:
    # Can lead to loss of precision.
    phase_unwrapped = unwrap(phase_angle, axis=time_axis)
    dphase = diff(phase_unwrapped, axis=time_axis)
  else:
    # Keep dphase bounded. N.B. runs faster than a single mod-2pi expression.
    dphase = diff(phase_angle, axis=time_axis)
    dphase = tf.where(dphase > np.pi, dphase - 2 * np.pi, dphase)
    dphase = tf.where(dphase < -np.pi, dphase + 2 * np.pi, dphase)

  # Add an initial phase to dphase.
  size = phase_angle.get_shape().as_list()
  size[time_axis] = 1
  begin = [0 for unused_s in size]
  phase_slice = tf.slice(phase_angle, begin, size)
  dphase = tf.concat([phase_slice, dphase], axis=time_axis) / np.pi
  return dphase


def polar2rect(mag, phase_angle):
  """Convert polar-form complex number to its rectangular form."""
  mag = tf.complex(mag, tf.convert_to_tensor(0.0, dtype=mag.dtype))
  phase = tf.complex(tf.cos(phase_angle), tf.sin(phase_angle))
  return mag * phase


def random_phase_in_radians(shape, dtype):
  return np.pi * (2 * tf.random_uniform(shape, dtype=dtype) - 1.0)


def crop_or_pad(waves, length, channels):
  """Crop or pad wave to have shape [N, length, channels].
  Args:
    waves: A 3D `Tensor` of NLC format.
    length: A Python scalar. The output wave size.
    channels: Number of output waves channels.
  Returns:
    A 3D `Tensor` of NLC format with shape [N, length, channels].
  """
  waves = tf.convert_to_tensor(waves)
  batch_size = int(waves.shape[0])
  waves_shape = tf.shape(waves)

  # Force audio length.
  pad = tf.maximum(0, length - waves_shape[1])
  right_pad = tf.to_int32(tf.to_float(pad) / 2.0)
  left_pad = pad - right_pad
  waves = tf.pad(waves, [[0, 0], [left_pad, right_pad], [0, 0]])
  waves = waves[:, :length, :]

  # Force number of channels.
  num_repeats = tf.to_int32(
      tf.ceil(tf.to_float(channels) / tf.to_float(waves_shape[2])))
  waves = tf.tile(waves, [1, 1, num_repeats])[:, :, :channels]

  waves.set_shape([batch_size, length, channels])
  return waves

class SpecgramsHelper(object):
  """Helper functions to compute specgrams."""

  def __init__(self, audio_length, spec_shape, overlap,
               sample_rate, mel_downscale, ifreq=True, discard_dc=True):
    self._audio_length = audio_length
    self._spec_shape = spec_shape
    self._overlap = overlap
    self._sample_rate = sample_rate
    self._mel_downscale = mel_downscale
    self._ifreq = ifreq
    self._discard_dc = discard_dc

    self._nfft, self._nhop = self._get_nfft_nhop()
    self._pad_l, self._pad_r = self._get_padding()

    self._eps = 1.0e-6

  def _safe_log(self, x):
    return tf.log(x + self._eps)

  def _get_nfft_nhop(self):
    n_freq_bins = self._spec_shape[1]
    # Power of two only has 1 nonzero in binary representation
    is_power_2 = bin(n_freq_bins).count('1') == 1
    if not is_power_2:
      raise ValueError('Wrong spec_shape. Number of frequency bins must be '
                       'a power of 2, not %d' % n_freq_bins)
    nfft = n_freq_bins * 2
    nhop = int((1. - self._overlap) * nfft)
    return (nfft, nhop)

  def _get_padding(self):
    """Infer left and right padding for STFT."""
    n_samps_inv = self._nhop * (self._spec_shape[0] - 1) + self._nfft
    if n_samps_inv < self._audio_length:
      raise ValueError('Wrong audio length. Number of ISTFT samples, %d, should'
                       ' be less than audio lengeth %d' % self._audio_length)

    # For Nsynth dataset, we are putting all padding in the front
    # This causes edge effects in the tail
    padding = n_samps_inv - self._audio_length
    padding_l = padding
    padding_r = padding - padding_l
    return padding_l, padding_r

  def waves_to_stfts(self, waves):
    """Convert from waves to complex stfts.
    Args:
      waves: Tensor of the waveform, shape [batch, time, 1].
    Returns:
      stfts: Complex64 tensor of stft, shape [batch, time, freq, 1].
    """
    waves_padded = tf.pad(waves, [[0, 0], [self._pad_l, self._pad_r], [0, 0]])
    stfts = tf.signal.stft(
        waves_padded[:, :, 0],
        frame_length=self._nfft,
        frame_step=self._nhop,
        fft_length=self._nfft,
        pad_end=False)[:, :, :, tf.newaxis]
    stfts = stfts[:, :, 1:] if self._discard_dc else stfts[:, :, :-1]
    stft_shape = stfts.get_shape().as_list()[1:3]
    if tuple(stft_shape) != tuple(self._spec_shape):
      raise ValueError(
          'Spectrogram returned the wrong shape {}, is not the same as the '
          'constructor spec_shape {}.'.format(stft_shape, self._spec_shape))
    return stfts

  def stfts_to_waves(self, stfts):
    """Convert from complex stfts to waves.
    Args:
      stfts: Complex64 tensor of stft, shape [batch, time, freq, 1].
    Returns:
      waves: Tensor of the waveform, shape [batch, time, 1].
    """
    dc = 1 if self._discard_dc else 0
    nyq = 1 - dc
    stfts = tf.pad(stfts, [[0, 0], [0, 0], [dc, nyq], [0, 0]])
    waves_resyn = tf.signal.inverse_stft(
        stfts=stfts[:, :, :, 0],
        frame_length=self._nfft,
        frame_step=self._nhop,
        fft_length=self._nfft,
        window_fn=tf.signal.inverse_stft_window_fn(
            frame_step=self._nhop))[:, :, tf.newaxis]
    # Python does not allow rslice of -0
    if self._pad_r == 0:
      return waves_resyn[:, self._pad_l:]
    else:
      return waves_resyn[:, self._pad_l:-self._pad_r]

  def stfts_to_specgrams(self, stfts):
    """Converts stfts to specgrams.
    Args:
      stfts: Complex64 tensor of stft, shape [batch, time, freq, 1].
    Returns:
      specgrams: Tensor of log magnitudes and instantaneous frequencies,
        shape [batch, time, freq, 2].
    """
    stfts = stfts[:, :, :, 0]

    logmag = self._safe_log(tf.abs(stfts))

    phase_angle = tf.angle(stfts)
    if self._ifreq:
      p = instantaneous_frequency(phase_angle)
    else:
      p = phase_angle / np.pi

    return tf.concat(
        [logmag[:, :, :, tf.newaxis], p[:, :, :, tf.newaxis]], axis=-1)

  def specgrams_to_stfts(self, specgrams):
    """Converts specgrams to stfts.
    Args:
      specgrams: Tensor of log magnitudes and instantaneous frequencies,
        shape [batch, time, freq, 2].
    Returns:
      stfts: Complex64 tensor of stft, shape [batch, time, freq, 1].
    """
    logmag = specgrams[:, :, :, 0]
    p = specgrams[:, :, :, 1]

    mag = tf.exp(logmag)

    if self._ifreq:
      phase_angle = tf.cumsum(p * np.pi, axis=-2)
    else:
      phase_angle = p * np.pi

    return polar2rect(mag, phase_angle)[:, :, :, tf.newaxis]

  def _linear_to_mel_matrix(self):
    """Get the mel transformation matrix."""
    num_freq_bins = self._nfft // 2
    lower_edge_hertz = 0.0
    upper_edge_hertz = self._sample_rate / 2.0
    num_mel_bins = num_freq_bins // self._mel_downscale
    return linear_to_mel_weight_matrix(
        num_mel_bins, num_freq_bins, self._sample_rate, lower_edge_hertz,
        upper_edge_hertz)

  def _mel_to_linear_matrix(self):
    """Get the inverse mel transformation matrix."""
    m = self._linear_to_mel_matrix()
    m_t = np.transpose(m)
    p = np.matmul(m, m_t)
    d = [1.0 / x if np.abs(x) > 1.0e-8 else x for x in np.sum(p, axis=0)]
    return np.matmul(m_t, np.diag(d))

  def specgrams_to_melspecgrams(self, specgrams):
    """Converts specgrams to melspecgrams.
    Args:
      specgrams: Tensor of log magnitudes and instantaneous frequencies,
        shape [batch, time, freq, 2].
    Returns:
      melspecgrams: Tensor of log magnitudes and instantaneous frequencies,
        shape [batch, time, freq, 2], mel scaling of frequencies.
    """
    if self._mel_downscale is None:
      return specgrams

    logmag = specgrams[:, :, :, 0]
    p = specgrams[:, :, :, 1]

    mag2 = tf.exp(2.0 * logmag)
    phase_angle = tf.cumsum(p * np.pi, axis=-2)

    l2mel = tf.to_float(self._linear_to_mel_matrix())
    logmelmag2 = self._safe_log(tf.tensordot(mag2, l2mel, 1))
    mel_phase_angle = tf.tensordot(phase_angle, l2mel, 1)
    mel_p = instantaneous_frequency(mel_phase_angle)

    return tf.concat(
        [logmelmag2[:, :, :, tf.newaxis], mel_p[:, :, :, tf.newaxis]], axis=-1)

  def melspecgrams_to_specgrams(self, melspecgrams):
    """Converts melspecgrams to specgrams.
    Args:
      melspecgrams: Tensor of log magnitudes and instantaneous frequencies,
        shape [batch, time, freq, 2], mel scaling of frequencies.
    Returns:
      specgrams: Tensor of log magnitudes and instantaneous frequencies,
        shape [batch, time, freq, 2].
    """
    if self._mel_downscale is None:
      return melspecgrams

    logmelmag2 = melspecgrams[:, :, :, 0]
    mel_p = melspecgrams[:, :, :, 1]

    mel2l = tf.to_float(self._mel_to_linear_matrix())
    mag2 = tf.tensordot(tf.exp(logmelmag2), mel2l, 1)
    logmag = 0.5 * self._safe_log(mag2)
    mel_phase_angle = tf.cumsum(mel_p * np.pi, axis=-2)
    phase_angle = tf.tensordot(mel_phase_angle, mel2l, 1)
    p = instantaneous_frequency(phase_angle)

    return tf.concat(
        [logmag[:, :, :, tf.newaxis], p[:, :, :, tf.newaxis]], axis=-1)

  def stfts_to_melspecgrams(self, stfts):
    """Converts stfts to mel-spectrograms."""
    return self.specgrams_to_melspecgrams(self.stfts_to_specgrams(stfts))

  def melspecgrams_to_stfts(self, melspecgrams):
    """Converts mel-spectrograms to stfts."""
    return self.specgrams_to_stfts(self.melspecgrams_to_specgrams(melspecgrams))

  def waves_to_specgrams(self, waves):
    """Converts waves to spectrograms."""
    return self.stfts_to_specgrams(self.waves_to_stfts(waves))

  def specgrams_to_waves(self, specgrams):
    """Converts spectrograms to stfts."""
    return self.stfts_to_waves(self.specgrams_to_stfts(specgrams))

  def waves_to_melspecgrams(self, waves):
    """Converts waves to mel-spectrograms."""
    return self.stfts_to_melspecgrams(self.waves_to_stfts(waves))

  def melspecgrams_to_waves(self, melspecgrams):
    """Converts mel-spectrograms to stfts."""
    return self.stfts_to_waves(self.melspecgrams_to_stfts(melspecgrams))