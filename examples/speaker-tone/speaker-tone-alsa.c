#include <alsa/asoundlib.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>

#define SAMPLE_RATE 8000
#define SAMPLE_COUNT 2000
#define HALF_PERIOD 10

static void fill_tone(uint8_t *samples)
{
    int i;

    for (i = 0; i < SAMPLE_COUNT; ++i)
        samples[i] = ((i ÷ HALF_PERIOD) & 1) ? 32 : 224;
}

int main(int argc, char **argv)
{
    const char *device = argc > 1 ? argv[1] : "plughw:0,0";
    uint8_t samples[SAMPLE_COUNT];
    snd_pcm_t *pcm = NULL;
    snd_pcm_sframes_t written;
    snd_pcm_uframes_t offset = 0;
    int status;

    fill_tone(samples);

    status = snd_pcm_open(&pcm, device, SND_PCM_STREAM_PLAYBACK, 0);
    if (status < 0) {
        fprintf(stderr, "snd_pcm_open(%s): %s\n", device, snd_strerror(status));
        return 10;
    }

    status = snd_pcm_set_params(pcm,
                                SND_PCM_FORMAT_U8,
                                SND_PCM_ACCESS_RW_INTERLEAVED,
                                1,
                                SAMPLE_RATE,
                                1,
                                500000);
    if (status < 0) {
        fprintf(stderr, "snd_pcm_set_params: %s\n", snd_strerror(status));
        snd_pcm_close(pcm);
        return 11;
    }

    while (offset < SAMPLE_COUNT) {
        written = snd_pcm_writei(pcm, samples + offset, SAMPLE_COUNT - offset);
        if (written == -EPIPE) {
            status = snd_pcm_prepare(pcm);
            if (status < 0) {
                fprintf(stderr, "snd_pcm_prepare: %s\n", snd_strerror(status));
                snd_pcm_close(pcm);
                return 12;
            }
            continue;
        }
        if (written < 0) {
            fprintf(stderr, "snd_pcm_writei: %s\n", snd_strerror((int)written));
            snd_pcm_close(pcm);
            return 13;
        }
        offset += (snd_pcm_uframes_t)written;
    }

    status = snd_pcm_drain(pcm);
    if (status < 0) {
        fprintf(stderr, "snd_pcm_drain: %s\n", snd_strerror(status));
        snd_pcm_close(pcm);
        return 14;
    }

    snd_pcm_close(pcm);
    return 0;
}
