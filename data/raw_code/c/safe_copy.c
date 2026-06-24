#include <stdio.h>
#include <string.h>

int safe_copy(char *dst, size_t dst_len, const char *src) {
    if (!dst || !src || dst_len == 0) return -1;
    snprintf(dst, dst_len, "%s", src);
    return 0;
}
