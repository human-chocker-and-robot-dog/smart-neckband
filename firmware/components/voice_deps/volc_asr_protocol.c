#include "volc_asr_protocol.h"

#include <string.h>

#define VOLC_PROTOCOL_VERSION 1U
#define VOLC_HEADER_WORDS 1U
#define VOLC_SERIALIZATION_NONE 0U
#define VOLC_SERIALIZATION_JSON 1U
#define VOLC_COMPRESSION_NONE 0U
#define VOLC_FLAG_SEQUENCE 1U
#define VOLC_FLAG_LAST 2U

static void write_be32(uint8_t *destination, uint32_t value)
{
    destination[0] = (uint8_t)(value >> 24U);
    destination[1] = (uint8_t)(value >> 16U);
    destination[2] = (uint8_t)(value >> 8U);
    destination[3] = (uint8_t)value;
}

static uint32_t read_be32(const uint8_t *source)
{
    return ((uint32_t)source[0] << 24U) |
           ((uint32_t)source[1] << 16U) |
           ((uint32_t)source[2] << 8U) |
           (uint32_t)source[3];
}

static size_t build_frame(uint8_t *out,
                          size_t capacity,
                          uint8_t message_type,
                          uint8_t flags,
                          uint8_t serialization,
                          const uint8_t *payload,
                          size_t payload_length)
{
    if (out == NULL || payload == NULL ||
        payload_length > UINT32_MAX ||
        capacity < V0_VOLC_ASR_PREFIX_SIZE + payload_length) {
        return 0U;
    }

    out[0] = (uint8_t)((VOLC_PROTOCOL_VERSION << 4U) | VOLC_HEADER_WORDS);
    out[1] = (uint8_t)((message_type << 4U) | flags);
    out[2] = (uint8_t)((serialization << 4U) | VOLC_COMPRESSION_NONE);
    out[3] = 0U;
    write_be32(&out[4], (uint32_t)payload_length);
    memcpy(&out[V0_VOLC_ASR_PREFIX_SIZE], payload, payload_length);
    return V0_VOLC_ASR_PREFIX_SIZE + payload_length;
}

size_t v0_volc_asr_build_full_request(uint8_t *out,
                                      size_t capacity,
                                      const char *json,
                                      size_t json_length)
{
    return build_frame(out,
                       capacity,
                       V0_VOLC_ASR_MESSAGE_FULL_REQUEST,
                       0U,
                       VOLC_SERIALIZATION_JSON,
                       (const uint8_t *)json,
                       json_length);
}

size_t v0_volc_asr_build_audio(uint8_t *out,
                              size_t capacity,
                              const int16_t *pcm,
                              size_t sample_count,
                              bool final_packet)
{
    if (sample_count > SIZE_MAX / sizeof(*pcm)) {
        return 0U;
    }
    return build_frame(out,
                       capacity,
                       V0_VOLC_ASR_MESSAGE_AUDIO,
                       final_packet ? VOLC_FLAG_LAST : 0U,
                       VOLC_SERIALIZATION_NONE,
                       (const uint8_t *)pcm,
                       sample_count * sizeof(*pcm));
}

bool v0_volc_asr_parse_server_frame(const uint8_t *frame,
                                    size_t frame_length,
                                    v0_volc_asr_frame_view_t *out_view)
{
    if (frame == NULL || out_view == NULL ||
        frame_length < V0_VOLC_ASR_HEADER_SIZE) {
        return false;
    }

    const uint8_t version = frame[0] >> 4U;
    const size_t header_size = (size_t)(frame[0] & 0x0fU) * 4U;
    if (version != VOLC_PROTOCOL_VERSION ||
        header_size < V0_VOLC_ASR_HEADER_SIZE ||
        header_size > frame_length) {
        return false;
    }

    memset(out_view, 0, sizeof(*out_view));
    out_view->message_type = frame[1] >> 4U;
    out_view->flags = frame[1] & 0x0fU;
    out_view->serialization = frame[2] >> 4U;
    out_view->compression = frame[2] & 0x0fU;
    if (out_view->compression != VOLC_COMPRESSION_NONE) {
        return false;
    }

    size_t offset = header_size;
    if (out_view->message_type == V0_VOLC_ASR_MESSAGE_ERROR) {
        if (frame_length - offset < 8U) {
            return false;
        }
        out_view->error_code = read_be32(&frame[offset]);
        offset += 4U;
    } else if ((out_view->flags & VOLC_FLAG_SEQUENCE) != 0U) {
        if (frame_length - offset < 8U) {
            return false;
        }
        out_view->sequence = (int32_t)read_be32(&frame[offset]);
        offset += 4U;
    }

    if (frame_length - offset < 4U) {
        return false;
    }
    const uint32_t payload_length = read_be32(&frame[offset]);
    offset += 4U;
    if ((size_t)payload_length != frame_length - offset) {
        return false;
    }
    out_view->payload = &frame[offset];
    out_view->payload_length = payload_length;
    return true;
}

bool v0_volc_asr_protocol_self_test(void)
{
    static const uint8_t expected_request[] = {
        0x11U, 0x10U, 0x10U, 0x00U,
        0x00U, 0x00U, 0x00U, 0x02U,
        0x7bU, 0x7dU,
    };
    uint8_t request[sizeof(expected_request)] = {0};
    if (v0_volc_asr_build_full_request(
            request, sizeof(request), "{}", 2U) != sizeof(request) ||
        memcmp(request, expected_request, sizeof(request)) != 0) {
        return false;
    }

    static const uint8_t response[] = {
        0x11U, 0x91U, 0x10U, 0x00U,
        0x00U, 0x00U, 0x00U, 0x07U,
        0x00U, 0x00U, 0x00U, 0x02U,
        0x7bU, 0x7dU,
    };
    v0_volc_asr_frame_view_t view = {0};
    return v0_volc_asr_parse_server_frame(
               response, sizeof(response), &view) &&
           view.message_type == V0_VOLC_ASR_MESSAGE_FULL_RESPONSE &&
           view.sequence == 7 &&
           view.payload_length == 2U &&
           memcmp(view.payload, "{}", 2U) == 0;
}
