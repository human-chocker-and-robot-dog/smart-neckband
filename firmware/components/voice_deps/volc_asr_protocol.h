#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define V0_VOLC_ASR_HEADER_SIZE 4U
#define V0_VOLC_ASR_PREFIX_SIZE 8U
#define V0_VOLC_ASR_MAX_RESPONSE_BYTES 8192U

typedef enum {
    V0_VOLC_ASR_MESSAGE_FULL_REQUEST = 1U,
    V0_VOLC_ASR_MESSAGE_AUDIO = 2U,
    V0_VOLC_ASR_MESSAGE_FULL_RESPONSE = 9U,
    V0_VOLC_ASR_MESSAGE_ACK = 11U,
    V0_VOLC_ASR_MESSAGE_ERROR = 15U,
} v0_volc_asr_message_type_t;

typedef struct {
    uint8_t message_type;
    uint8_t flags;
    uint8_t serialization;
    uint8_t compression;
    int32_t sequence;
    uint32_t error_code;
    const uint8_t *payload;
    size_t payload_length;
} v0_volc_asr_frame_view_t;

size_t v0_volc_asr_build_full_request(uint8_t *out,
                                      size_t capacity,
                                      const char *json,
                                      size_t json_length);
size_t v0_volc_asr_build_audio(uint8_t *out,
                              size_t capacity,
                              const int16_t *pcm,
                              size_t sample_count,
                              bool final_packet);
bool v0_volc_asr_parse_server_frame(const uint8_t *frame,
                                    size_t frame_length,
                                    v0_volc_asr_frame_view_t *out_view);
bool v0_volc_asr_protocol_self_test(void);

#ifdef __cplusplus
}
#endif
