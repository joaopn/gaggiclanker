/* Force-included into every simulator translation unit by scripts/sim.sh.
 *
 * The GaggiMate simulator is developed against Apple clang, whose libc++
 * headers pull in far more than they promise. GCC's libstdc++ does not, so on
 * Linux the firmware source fails to compile over transitively-included
 * headers it never asked for: <memory> (BLEScalePlugin.h's std::unique_ptr),
 * <stdexcept> (core/utils.h's std::runtime_error), <cstdarg> (the Arduino
 * Print shim's va_start).
 *
 * Patching the firmware checkout is not ours to do — it is a read-only
 * reference clone of somebody else's project — so the missing includes are
 * added from outside instead. The guard matters: PlatformIO passes one flag
 * list to both the C and the C++ compiler, and a bare `-include memory` is a
 * fatal error for every .c file in the tree.
 */
#ifdef __cplusplus
#include <algorithm>
#include <cstdarg>
#include <cstdint>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#endif
