// DuskLaunchBeacon.m — a pre-main load beacon (STEREO-3D-GUIDE §8 / §9.5 #38).
//
// The SwiftUI @main graft rewrites the process entry, and an OTA-only visionOS
// app that dies before main() leaves no app log to explain why (this is exactly
// what "cracked vkQuake's silent OTA death"). A `constructor` runs at dylib load,
// before any app code, and writes one timestamped line to Documents — so a launch
// that never reaches the game still leaves proof the binary loaded and which
// build it was. Cheap, always-on, no dependency on the engine being up.

#import <Foundation/Foundation.h>

__attribute__((constructor)) static void DuskLaunchBeacon(void) {
    @autoreleasepool {
        NSArray<NSURL*>* docs =
            [NSFileManager.defaultManager URLsForDirectory:NSDocumentDirectory
                                                 inDomains:NSUserDomainMask];
        NSURL* dir = docs.firstObject;
        if (dir == nil) {
            return;
        }
        NSURL* file = [dir URLByAppendingPathComponent:@"dusk-launch-beacon.log"];
        NSString* line = [NSString
            stringWithFormat:@"%@  loaded  build %s %s\n",
                             NSDate.date, __DATE__, __TIME__];
        NSData* data = [line dataUsingEncoding:NSUTF8StringEncoding];
        NSFileHandle* fh = [NSFileHandle fileHandleForWritingAtPath:file.path];
        if (fh == nil) {
            [data writeToURL:file atomically:YES];
        } else {
            @try {
                [fh seekToEndOfFile];
                [fh writeData:data];
            } @catch (NSException* e) {
            }
            [fh closeFile];
        }
    }
}
