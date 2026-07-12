#define NOMINMAX
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>
#include <wincodec.h>
#include <propvarutil.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "mfreadwrite.lib")
#pragma comment(lib, "mfuuid.lib")
#pragma comment(lib, "mf.lib")
#pragma comment(lib, "windowscodecs.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")
#pragma comment(lib, "propsys.lib")

template <typename T>
class ComPtr {
public:
    ComPtr() = default;
    ~ComPtr() { reset(); }
    ComPtr(const ComPtr&) = delete;
    ComPtr& operator=(const ComPtr&) = delete;
    T* get() const { return ptr_; }
    T** put() {
        reset();
        return &ptr_;
    }
    T* operator->() const { return ptr_; }
    explicit operator bool() const { return ptr_ != nullptr; }
    void reset() {
        if (ptr_) {
            ptr_->Release();
            ptr_ = nullptr;
        }
    }
private:
    T* ptr_ = nullptr;
};

struct Config {
    int device = 0;
    int max_side = 1280;
    double fps = 3.0;
};

struct Frame {
    std::vector<BYTE> bgra;
    UINT32 width = 0;
    UINT32 height = 0;
    LONG stride = 0;
};

static void fail_hr(const char* what, HRESULT hr) {
    std::cerr << what << " failed hr=0x" << std::hex << static_cast<unsigned long>(hr) << std::dec << "\n";
}

static bool parse_args(int argc, char** argv, Config& cfg) {
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--version") {
            std::cout << "jarvis_vision_sidecar/0.1.0 media-foundation-wic\n";
            std::exit(0);
        }
        if (arg == "--device" && i + 1 < argc) {
            cfg.device = std::atoi(argv[++i]);
        } else if (arg == "--max-side" && i + 1 < argc) {
            cfg.max_side = std::max(64, std::atoi(argv[++i]));
        } else if (arg == "--fps" && i + 1 < argc) {
            cfg.fps = std::max(0.1, std::atof(argv[++i]));
        } else {
            std::cerr << "usage: jarvis_vision_sidecar.exe [--device N] [--max-side PX] [--fps N]\n";
            return false;
        }
    }
    return true;
}

static HRESULT create_source(int device_index, IMFMediaSource** out_source) {
    ComPtr<IMFAttributes> attrs;
    HRESULT hr = MFCreateAttributes(attrs.put(), 1);
    if (FAILED(hr)) return hr;
    hr = attrs->SetGUID(MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID);
    if (FAILED(hr)) return hr;

    IMFActivate** devices = nullptr;
    UINT32 count = 0;
    hr = MFEnumDeviceSources(attrs.get(), &devices, &count);
    if (FAILED(hr)) return hr;
    std::unique_ptr<IMFActivate*, decltype(&CoTaskMemFree)> guard(devices, CoTaskMemFree);
    if (count == 0 || device_index < 0 || static_cast<UINT32>(device_index) >= count) {
        std::cerr << "camera index " << device_index << " not available; devices=" << count << "\n";
        return MF_E_NOT_FOUND;
    }
    hr = devices[device_index]->ActivateObject(IID_PPV_ARGS(out_source));
    for (UINT32 i = 0; i < count; ++i) {
        devices[i]->Release();
    }
    return hr;
}

static HRESULT create_reader(IMFMediaSource* source, IMFSourceReader** out_reader, UINT32& width, UINT32& height) {
    ComPtr<IMFSourceReader> reader;
    HRESULT hr = MFCreateSourceReaderFromMediaSource(source, nullptr, reader.put());
    if (FAILED(hr)) return hr;

    ComPtr<IMFMediaType> type;
    hr = MFCreateMediaType(type.put());
    if (FAILED(hr)) return hr;
    hr = type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    if (FAILED(hr)) return hr;
    hr = type->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
    if (FAILED(hr)) return hr;
    hr = reader->SetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, type.get());
    if (FAILED(hr)) return hr;

    ComPtr<IMFMediaType> current;
    hr = reader->GetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, current.put());
    if (FAILED(hr)) return hr;
    hr = MFGetAttributeSize(current.get(), MF_MT_FRAME_SIZE, &width, &height);
    if (FAILED(hr)) return hr;

    LONG stride = 0;
    MFGetStrideForBitmapInfoHeader(MFVideoFormat_RGB32.Data1, width, &stride);
    current->SetUINT32(MF_MT_DEFAULT_STRIDE, static_cast<UINT32>(stride));

    *out_reader = reader.get();
    (*out_reader)->AddRef();
    return S_OK;
}

static HRESULT read_frame(IMFSourceReader* reader, UINT32 width, UINT32 height, Frame& out) {
    for (;;) {
        DWORD stream_index = 0;
        DWORD flags = 0;
        LONGLONG timestamp = 0;
        ComPtr<IMFSample> sample;
        HRESULT hr = reader->ReadSample(
            MF_SOURCE_READER_FIRST_VIDEO_STREAM,
            0,
            &stream_index,
            &flags,
            &timestamp,
            sample.put()
        );
        if (FAILED(hr)) return hr;
        if (flags & MF_SOURCE_READERF_ENDOFSTREAM) return MF_E_END_OF_STREAM;
        if (!sample) continue;

        ComPtr<IMFMediaBuffer> buffer;
        hr = sample->ConvertToContiguousBuffer(buffer.put());
        if (FAILED(hr)) return hr;

        BYTE* data = nullptr;
        DWORD max_len = 0;
        DWORD cur_len = 0;
        hr = buffer->Lock(&data, &max_len, &cur_len);
        if (FAILED(hr)) return hr;

        const LONG stride = static_cast<LONG>(width * 4);
        const size_t expected = static_cast<size_t>(stride) * height;
        out.bgra.resize(expected);
        const size_t copy_len = std::min(expected, static_cast<size_t>(cur_len));
        std::memcpy(out.bgra.data(), data, copy_len);
        if (copy_len < expected) {
            std::fill(out.bgra.begin() + copy_len, out.bgra.end(), 0);
        }
        buffer->Unlock();

        out.width = width;
        out.height = height;
        out.stride = stride;
        return S_OK;
    }
}

static HRESULT encode_jpeg_wic(
    IWICImagingFactory* factory,
    const Frame& frame,
    int max_side,
    std::vector<BYTE>& jpeg,
    UINT32& out_width,
    UINT32& out_height
) {
    ComPtr<IWICBitmap> bitmap;
    HRESULT hr = factory->CreateBitmapFromMemory(
        frame.width,
        frame.height,
        GUID_WICPixelFormat32bppBGR,
        static_cast<UINT>(frame.stride),
        static_cast<UINT>(frame.bgra.size()),
        const_cast<BYTE*>(frame.bgra.data()),
        bitmap.put()
    );
    if (FAILED(hr)) return hr;

    IWICBitmapSource* source = bitmap.get();
    ComPtr<IWICBitmapScaler> scaler;
    out_width = frame.width;
    out_height = frame.height;
    const UINT32 longest = std::max(frame.width, frame.height);
    if (longest > static_cast<UINT32>(max_side)) {
        const double scale = static_cast<double>(max_side) / static_cast<double>(longest);
        out_width = std::max<UINT32>(1, static_cast<UINT32>(frame.width * scale));
        out_height = std::max<UINT32>(1, static_cast<UINT32>(frame.height * scale));
        hr = factory->CreateBitmapScaler(scaler.put());
        if (FAILED(hr)) return hr;
        hr = scaler->Initialize(bitmap.get(), out_width, out_height, WICBitmapInterpolationModeFant);
        if (FAILED(hr)) return hr;
        source = scaler.get();
    }

    ComPtr<IStream> stream;
    hr = CreateStreamOnHGlobal(nullptr, TRUE, stream.put());
    if (FAILED(hr)) return hr;

    ComPtr<IWICBitmapEncoder> encoder;
    hr = factory->CreateEncoder(GUID_ContainerFormatJpeg, nullptr, encoder.put());
    if (FAILED(hr)) return hr;
    hr = encoder->Initialize(stream.get(), WICBitmapEncoderNoCache);
    if (FAILED(hr)) return hr;

    ComPtr<IWICBitmapFrameEncode> frame_encode;
    ComPtr<IPropertyBag2> props;
    hr = encoder->CreateNewFrame(frame_encode.put(), props.put());
    if (FAILED(hr)) return hr;
    PROPBAG2 option = {};
    option.pstrName = const_cast<LPOLESTR>(L"ImageQuality");
    VARIANT value;
    VariantInit(&value);
    value.vt = VT_R4;
    value.fltVal = 0.85f;
    if (props) {
        props->Write(1, &option, &value);
    }
    VariantClear(&value);

    hr = frame_encode->Initialize(props.get());
    if (FAILED(hr)) return hr;
    hr = frame_encode->SetSize(out_width, out_height);
    if (FAILED(hr)) return hr;
    WICPixelFormatGUID format = GUID_WICPixelFormat24bppBGR;
    hr = frame_encode->SetPixelFormat(&format);
    if (FAILED(hr)) return hr;
    hr = frame_encode->WriteSource(source, nullptr);
    if (FAILED(hr)) return hr;
    hr = frame_encode->Commit();
    if (FAILED(hr)) return hr;
    hr = encoder->Commit();
    if (FAILED(hr)) return hr;

    HGLOBAL hglobal = nullptr;
    hr = GetHGlobalFromStream(stream.get(), &hglobal);
    if (FAILED(hr)) return hr;
    SIZE_T size = GlobalSize(hglobal);
    void* data = GlobalLock(hglobal);
    if (!data || size == 0) return E_FAIL;
    jpeg.assign(static_cast<BYTE*>(data), static_cast<BYTE*>(data) + size);
    GlobalUnlock(hglobal);
    return S_OK;
}

#pragma pack(push, 1)
struct PacketHeader {
    char magic[4];
    uint32_t width;
    uint32_t height;
    uint32_t payload_len;
    uint64_t timestamp_ns;
};
#pragma pack(pop)

static bool write_packet(const std::vector<BYTE>& jpeg, UINT32 width, UINT32 height) {
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    PacketHeader header = {
        {'J', 'V', 'F', '1'},
        width,
        height,
        static_cast<uint32_t>(jpeg.size()),
        static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(now).count())
    };
    FILE* out = stdout;
    if (std::fwrite(&header, 1, sizeof(header), out) != sizeof(header)) return false;
    if (!jpeg.empty() && std::fwrite(jpeg.data(), 1, jpeg.size(), out) != jpeg.size()) return false;
    std::fflush(out);
    return true;
}

int main(int argc, char** argv) {
    Config cfg;
    if (!parse_args(argc, argv, cfg)) return 2;

    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr)) {
        fail_hr("CoInitializeEx", hr);
        return 1;
    }
    hr = MFStartup(MF_VERSION);
    if (FAILED(hr)) {
        fail_hr("MFStartup", hr);
        CoUninitialize();
        return 1;
    }

    ComPtr<IWICImagingFactory> factory;
    hr = CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(factory.put()));
    if (FAILED(hr)) {
        fail_hr("CoCreateInstance(WIC)", hr);
        MFShutdown();
        CoUninitialize();
        return 1;
    }

    ComPtr<IMFMediaSource> source;
    hr = create_source(cfg.device, source.put());
    if (FAILED(hr)) {
        fail_hr("create_source", hr);
        MFShutdown();
        CoUninitialize();
        return 1;
    }

    UINT32 width = 0;
    UINT32 height = 0;
    ComPtr<IMFSourceReader> reader;
    hr = create_reader(source.get(), reader.put(), width, height);
    if (FAILED(hr)) {
        fail_hr("create_reader", hr);
        source->Shutdown();
        MFShutdown();
        CoUninitialize();
        return 1;
    }

    const auto period = std::chrono::duration<double>(1.0 / std::max(0.1, cfg.fps));
    auto next_tick = std::chrono::steady_clock::now();
    for (;;) {
        Frame raw;
        hr = read_frame(reader.get(), width, height, raw);
        if (FAILED(hr)) {
            fail_hr("read_frame", hr);
            break;
        }
        std::vector<BYTE> jpeg;
        UINT32 out_w = 0;
        UINT32 out_h = 0;
        hr = encode_jpeg_wic(factory.get(), raw, cfg.max_side, jpeg, out_w, out_h);
        if (FAILED(hr)) {
            fail_hr("encode_jpeg_wic", hr);
            break;
        }
        if (!write_packet(jpeg, out_w, out_h)) {
            break;
        }
        next_tick += std::chrono::duration_cast<std::chrono::steady_clock::duration>(period);
        std::this_thread::sleep_until(next_tick);
        if (next_tick < std::chrono::steady_clock::now() - std::chrono::seconds(1)) {
            next_tick = std::chrono::steady_clock::now();
        }
    }

    source->Shutdown();
    MFShutdown();
    CoUninitialize();
    return 0;
}
