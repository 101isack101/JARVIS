// jarvis_audio_native.cpp
//
// Small C ABI for Jarvis' Python audio pipeline. The first native target is AEC:
// a time-domain NLMS echo canceller for the far-end reference used by wake-word
// barge-in. It is intentionally dependency-free so it can be built as a tiny DLL
// and loaded from Python via ctypes.

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <new>
#include <vector>

#if defined(_WIN32)
#define JARVIS_API extern "C" __declspec(dllexport)
#else
#define JARVIS_API extern "C" __attribute__((visibility("default")))
#endif

namespace {

struct JarvisAec {
    int block = 512;
    int taps = 4096;
    float mu = 0.3f;
    float eps = 1.0e-6f;
    float dtd_factor = 0.5f;
    float converge_thresh_db = 6.0f;
    bool converged = false;
    float last_erle_db = 0.0f;
    int ring_pos = 0;
    std::vector<float> weights;
    std::vector<float> far_ring;

    JarvisAec(
        int block_,
        int taps_,
        float mu_,
        float eps_,
        float dtd_factor_,
        float converge_thresh_db_
    )
        : block(std::max(16, block_)),
          taps(std::max(16, taps_)),
          mu(mu_),
          eps(std::max(1.0e-12f, eps_)),
          dtd_factor(dtd_factor_),
          converge_thresh_db(converge_thresh_db_),
          weights(static_cast<std::size_t>(std::max(16, taps_)), 0.0f),
          far_ring(static_cast<std::size_t>(std::max(16, taps_)), 0.0f) {}

    void reset() {
        std::fill(weights.begin(), weights.end(), 0.0f);
        std::fill(far_ring.begin(), far_ring.end(), 0.0f);
        ring_pos = 0;
        converged = false;
        last_erle_db = 0.0f;
    }

    void push_far(float sample) {
        far_ring[static_cast<std::size_t>(ring_pos)] = sample;
        ring_pos++;
        if (ring_pos >= taps) {
            ring_pos = 0;
        }
    }

    int ring_index_for_lag(int lag) const {
        int idx = ring_pos - 1 - lag;
        while (idx < 0) {
            idx += taps;
        }
        return idx;
    }

    float dot_echo() const {
        float y = 0.0f;
        for (int lag = 0; lag < taps; ++lag) {
            y += weights[static_cast<std::size_t>(lag)] *
                 far_ring[static_cast<std::size_t>(ring_index_for_lag(lag))];
        }
        return y;
    }

    float far_power() const {
        float p = eps;
        for (float x : far_ring) {
            p += x * x;
        }
        return p;
    }

    void adapt(float error, float norm) {
        const float step = mu * error / norm;
        for (int lag = 0; lag < taps; ++lag) {
            const int idx = ring_index_for_lag(lag);
            weights[static_cast<std::size_t>(lag)] +=
                step * far_ring[static_cast<std::size_t>(idx)];
        }
    }

    int process(const float* near, const float* far, int n, float* out) {
        if (!near || !far || !out || n <= 0) {
            return -1;
        }

        double near_pow = 0.0;
        double err_pow = 0.0;

        for (int i = 0; i < n; ++i) {
            push_far(far[i]);
            const float y = dot_echo();
            const float e = near[i] - y;
            out[i] = std::max(-1.0f, std::min(1.0f, e));

            near_pow += static_cast<double>(near[i]) * static_cast<double>(near[i]);
            err_pow += static_cast<double>(e) * static_cast<double>(e);

            const float echo_pow = y * y;
            const bool double_talk =
                converged && (e * e) > (dtd_factor * (echo_pow + eps));
            if (!double_talk) {
                adapt(e, far_power());
            }
        }

        near_pow /= static_cast<double>(n);
        err_pow /= static_cast<double>(n);
        if (near_pow > 1.0e-12) {
            last_erle_db = static_cast<float>(
                10.0 * std::log10(near_pow / (err_pow + 1.0e-12))
            );
            if (last_erle_db > converge_thresh_db) {
                converged = true;
            }
        } else {
            last_erle_db = 0.0f;
        }
        return 0;
    }
};

}  // namespace

JARVIS_API const char* jarvis_audio_native_version() {
    return "jarvis_audio_native/0.1.0 nlms-aec";
}
JARVIS_API void* jarvis_aec_create(
    int block,
    int taps,
    float mu,
    float eps,
    float dtd_factor,
    float converge_thresh_db
) {
    try {
        return new JarvisAec(block, taps, mu, eps, dtd_factor, converge_thresh_db);
    } catch (const std::bad_alloc&) {
        return nullptr;
    } catch (...) {
        return nullptr;
    }
}

JARVIS_API void jarvis_aec_destroy(void* handle) {
    delete static_cast<JarvisAec*>(handle);
}

JARVIS_API void jarvis_aec_reset(void* handle) {
    if (!handle) {
        return;
    }
    static_cast<JarvisAec*>(handle)->reset();
}

JARVIS_API int jarvis_aec_process(
    void* handle,
    const float* near,
    const float* far,
    int n,
    float* out
) {
    if (!handle) {
        return -2;
    }
    return static_cast<JarvisAec*>(handle)->process(near, far, n, out);
}

JARVIS_API float jarvis_aec_last_erle_db(void* handle) {
    if (!handle) {
        return 0.0f;
    }
    return static_cast<JarvisAec*>(handle)->last_erle_db;
}

JARVIS_API int jarvis_resample_24k_to_16k_f32(
    const float* input,
    int input_len,
    float* output,
    int output_cap
) {
    if (!input || !output || input_len < 0 || output_cap < 0) {
        return -1;
    }
    if (input_len == 0) {
        return 0;
    }

    const int expected = (input_len * 2 + 2) / 3;  // ceil(n * 2 / 3)
    if (output_cap < expected) {
        return -2;
    }

    for (int i = 0; i < expected; ++i) {
        const float src_pos = static_cast<float>(i) * 1.5f;  // 24k / 16k
        const int j = static_cast<int>(std::floor(src_pos));
        const float frac = src_pos - static_cast<float>(j);
        const float a = input[std::min(j, input_len - 1)];
        const float b = input[std::min(j + 1, input_len - 1)];
        output[i] = a + (b - a) * frac;
    }
    return expected;
}
