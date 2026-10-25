# Dr.CU violation dump

`drcu_vio_dump.patch` makes [Dr.CU](https://github.com/cuhk-eda/dr-cu) write one `type,layer,xl,yl,xh,yh` row
(DBU) per violation to `$DRCU_VIO_DUMP` (needs `-multiNetVerbose MIDDLE`). It also fixes a Boost >= 1.77 clash and
adds `-DDRCU_STATIC=OFF`. Without it, placeviz still routes but draws no DRV boxes.

## Build

```bash
git clone https://github.com/cuhk-eda/dr-cu && cd dr-cu
git checkout 3d81988d741da36d2612f3443165970e0125bccc
git apply /path/to/placement-viz/tools/drcu_vio_dump.patch
mkdir build && cd build
cmake ../src -DCMAKE_BUILD_TYPE=Release -DDRCU_STATIC=OFF && make -j8 ispd19dr
```

## Config

```yaml
dr:
  name: Dr.CU
  cmd: "/path/to/dr-cu/build/ispd19dr -lef {lef0} -def {def} -guide {guide} -output {dr_def} -threads {threads}
        -tat 2000000000 -multiNetVerbose MIDDLE"
  env: {DRCU_VIO_DUMP: "{route_dir}/vio.csv"}
  vio_csv: "{route_dir}/vio.csv"
```

## Licence

The patch contains Dr.CU code and is under Dr.CU's CU-SD licence, not this repo's MIT licence:

```
Copyright (c) 2019-2021, The Chinese University of Hong Kong
All rights reserved.

CU-SD LICENSE (adapted from the original BSD license) Redistribution of the any code, with or without
modification, are permitted provided that the conditions below are met.

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the
   following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the
   following disclaimer in the documentation and/or other materials provided with the distribution.
3. Neither the name nor trademark of the copyright holder or the author may be used to endorse or promote
   products derived from this software without specific prior written permission.
4. Users are entirely responsible, to the exclusion of the author, for compliance with (a) regulations set by
   owners or administrators of employed equipment, (b) licensing terms of any other software, and (c) local,
   national, and international regulations regarding use, including those regarding import, export, and use of
   encryption software.

THIS FREE SOFTWARE IS PROVIDED BY THE AUTHOR "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO
EVENT SHALL THE AUTHOR OR ANY CONTRIBUTOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, EFFECTS OF UNAUTHORIZED OR MALICIOUS NETWORK ACCESS;
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
