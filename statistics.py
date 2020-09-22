#!/usr/bin/python3.8

# This file computes additional statistics over the produced dataset.
# Run this file if you want the base counts, pair-type counts, identity percents, etc
# in the database.
# This should be run from the folder where the file is (to access the database with path "results/RNANet.db")

import getopt, os, pickle, sqlite3, shlex, subprocess, sys
import numpy as np
import pandas as pd
import threading as th
import scipy.stats as st
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import matplotlib.patches as mpatches
import scipy.cluster.hierarchy as sch
from scipy.spatial.distance import squareform
from mpl_toolkits.mplot3d import axes3d
from Bio import AlignIO, SeqIO
from functools import partial
from multiprocessing import Pool, Manager
from os import path
from tqdm import tqdm
from collections import Counter
from RNAnet import Job, read_cpu_number, sql_ask_database, sql_execute, warn, notify, init_worker

path_to_3D_data = "tobedefinedbyoptions"
path_to_seq_data = "tobedefinedbyoptions"
res_thr = 20.0 # default: all structures

LSU_set = ("RF00002", "RF02540", "RF02541", "RF02543", "RF02546")   # From Rfam CLAN 00112
SSU_set = ("RF00177", "RF02542",  "RF02545", "RF01959", "RF01960")  # From Rfam CLAN 00111

def reproduce_wadley_results(carbon=4, show=False, sd_range=(1,4)):
    """
    Plot the joint distribution of pseudotorsion angles, in a Ramachandran-style graph.
    See Wadley & Pyle (2007)

    Arguments:
    show: True or False, call plt.show() at this end or not
    filter_helical: None, "form", "zone", or "both"
                    None: do not remove helical nucleotide
                    "form": remove nucleotides if they belong to a A, B or Z form stem
                    "zone": remove nucleotides falling in an arbitrary zone (see zone argument)
                    "both": remove nucleotides fulfilling one or both of the above conditions
    carbon: 1 or 4, use C4' (eta and theta) or C1' (eta_prime and theta_prime)
    sd_range: tuple, set values below avg + sd_range[0] * stdev to 0,
                     and values above avg + sd_range[1] * stdev to avg + sd_range[1] * stdev.
                     This removes noise and cuts too high peaks, to clearly see the clusters.
    """

    os.makedirs("results/figures/wadley_plots/", exist_ok=True)

    if carbon == 4:
        angle = "eta"
        xlabel = "$\\eta=C_4'^{i-1}-P^i-C_4'^i-P^{i+1}$"
        ylabel = "$\\theta=P^i-C_4'^i-P^{i+1}-C_4'^{i+1}$"
    elif carbon == 1:
        angle = "eta_prime"
        xlabel = "$\\eta'=C_1'^{i-1}-P^i-C_1'^i-P^{i+1}$"
        ylabel = "$\\theta'=P^i-C_1'^i-P^{i+1}-C_1'^{i+1}$"
    else:
        exit("You overestimate my capabilities !")

    
    if not path.isfile(f"data/wadley_kernel_{angle}_{res_thr}A.npz"):

        # Get a worker number to position the progress bar
        global idxQueue
        thr_idx = idxQueue.get()
        pbar = tqdm(total=2, desc=f"Worker {thr_idx+1}: eta/theta C{carbon} kernels", position=thr_idx+1, leave=False)

        # Extract the angle values of c2'-endo and c3'-endo nucleotides
        with sqlite3.connect("results/RNANet.db") as conn:
            df = pd.read_sql(f"""SELECT {angle}, th{angle} 
                                 FROM nucleotide JOIN (
                                    SELECT chain_id FROM chain JOIN structure
                                    WHERE structure.resolution <= {res_thr}
                                 ) AS c
                                 WHERE puckering="C2'-endo" 
                                    AND {angle} IS NOT NULL 
                                    AND th{angle} IS NOT NULL;""", conn)
            c2_endo_etas = df[angle].values.tolist()
            c2_endo_thetas = df["th"+angle].values.tolist()
            df = pd.read_sql(f"""SELECT {angle}, th{angle} 
                                 FROM nucleotide JOIN (
                                    SELECT chain_id FROM chain JOIN structure
                                    WHERE structure.resolution <= {res_thr}
                                 ) AS c
                                 WHERE form = '.' 
                                    AND puckering="C3'-endo" 
                                    AND {angle} IS NOT NULL 
                                    AND th{angle} IS NOT NULL;""", conn)
            c3_endo_etas = df[angle].values.tolist()
            c3_endo_thetas = df["th"+angle].values.tolist()
        
        # Create arrays with (x,y) coordinates of the points
        values_c3 = np.vstack([c3_endo_etas, c3_endo_thetas])
        values_c2 = np.vstack([c2_endo_etas, c2_endo_thetas])

        # Approximate the density by a gaussian kernel
        kernel_c3 = st.gaussian_kde(values_c3)
        kernel_c2 = st.gaussian_kde(values_c2)

        # Create 100x100 regular (x,y,z) values for the plot
        xx, yy = np.mgrid[0:2*np.pi:100j, 0:2*np.pi:100j]
        positions = np.vstack([xx.ravel(), yy.ravel()])
        f_c3 = np.reshape(kernel_c3(positions).T, xx.shape)
        pbar.update(1)
        f_c2 = np.reshape(kernel_c2(positions).T, xx.shape)
        pbar.update(1)

        # Save the data to an archive for later use without the need to recompute
        np.savez(f"data/wadley_kernel_{angle}.npz",
                  c3_endo_e=c3_endo_etas, c3_endo_t=c3_endo_thetas,
                  c2_endo_e=c2_endo_etas, c2_endo_t=c2_endo_thetas,
                  kernel_c3=f_c3, kernel_c2=f_c2)
        pbar.close()
        idxQueue.put(thr_idx)
    else:
        f = np.load(f"data/wadley_kernel_{angle}.npz")
        c2_endo_etas = f["c2_endo_e"]
        c3_endo_etas = f["c3_endo_e"]
        c2_endo_thetas = f["c2_endo_t"]
        c3_endo_thetas = f["c3_endo_t"]
        f_c3 = f["kernel_c3"]
        f_c2 = f["kernel_c2"]
        xx, yy = np.mgrid[0:2*np.pi:100j, 0:2*np.pi:100j]

    # notify(f"Kernel computed for {angle}/th{angle} (or loaded from file).")

    # exact counts:
    hist_c2, xedges, yedges = np.histogram2d(c2_endo_etas, c2_endo_thetas, bins=int(2*np.pi/0.1), 
                                             range=[[0, 2*np.pi], [0, 2*np.pi]])
    hist_c3, xedges, yedges = np.histogram2d(c3_endo_etas, c3_endo_thetas, bins=int(2*np.pi/0.1), 
                                             range=[[0, 2*np.pi], [0, 2*np.pi]])
    cmap = cm.get_cmap("jet")
    color_values = cmap(hist_c3.ravel()/hist_c3.max())

    for x, y, hist, f, l in zip( (c3_endo_etas, c2_endo_etas), 
                                 (c3_endo_thetas, c2_endo_thetas), 
                                 (hist_c3, hist_c2), 
                                 (f_c3, f_c2), ("c3","c2")):
        # cut hist and kernel
        hist_sup_thr = hist.mean() + sd_range[1]*hist.std()
        hist_cut = np.where( hist > hist_sup_thr, hist_sup_thr, hist)
        f_sup_thr = f.mean() + sd_range[1]*f.std()
        f_low_thr = f.mean() + sd_range[0]*f.std()
        f_cut = np.where(f > f_sup_thr, f_sup_thr, f)
        f_cut = np.where(f_cut < f_low_thr, 0, f_cut)
        levels = [f.mean()+f.std(), f.mean()+2*f.std(), f.mean()+4*f.std()]

        # histogram:
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        xpos, ypos = np.meshgrid(xedges[:-1], yedges[:-1], indexing="ij")
        ax.bar3d(xpos.ravel(), ypos.ravel(), 0.0, 0.09, 0.09, hist_cut.ravel(), color=color_values, zorder="max")
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        fig.savefig(f"results/figures/wadley_plots/wadley_hist_{angle}_{l}_{res_thr}A.png")
        if show:
            fig.show()
        plt.close()

        # Smoothed joint distribution
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        ax.plot_surface(xx, yy, f_cut, cmap=cm.get_cmap("coolwarm"), linewidth=0, antialiased=True)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        fig.savefig(f"results/figures/wadley_plots/wadley_distrib_{angle}_{l}_{res_thr}A.png")
        if show:
            fig.show()
        plt.close()

        # 2D Wadley plot
        fig = plt.figure(figsize=(5,5))
        ax = fig.gca()
        ax.scatter(x, y, s=1, alpha=0.1)
        ax.contourf(xx, yy, f_cut, alpha=0.5, cmap=cm.get_cmap("coolwarm"), levels=levels, extend="max")

        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        fig.savefig(f"results/figures/wadley_plots/wadley_{angle}_{l}_{res_thr}A.png")
        if show:
            fig.show()
        plt.close()
    # print(f"[{worker_nbr}]\tComputed joint distribution of angles (C{carbon}) and saved the figures.")

def stats_len():
    """Plots statistics on chain lengths in RNA families.
    
    REQUIRES tables chain, nucleotide up to date.
    """
    
    # Get a worker number to position the progress bar
    global idxQueue
    thr_idx = idxQueue.get()

    # sort the RNA families so that the plot is readable
    def family_order(f):
        if f in LSU_set:
            return 4
        elif f in SSU_set:
            return 3
        elif f in ["RF00001"]:      #
            return 1                # put tRNAs and 5S rRNAs first,
        elif f in ["RF00005"]:      # because of the logarithmic scale, otherwise, they look tiny
            return 0                #
        else:
            return 2
    
    fam_list.sort(key=family_order)

    cols = []
    lengths = []
    
    for i,f in enumerate(tqdm(fam_list, position=thr_idx+1, desc=f"Worker {thr_idx+1}: Average chain lengths", leave=False)):

        # Define a color for that family in the plot
        if f in LSU_set:
            cols.append("red") # LSU
        elif f in SSU_set:
            cols.append("blue") # SSU
        elif f in ["RF00001"]:
            cols.append("green")
        elif f in ["RF00005"]:
            cols.append("orange")
        else:
            cols.append("grey")

        # Get the lengths of chains
        with sqlite3.connect("results/RNANet.db") as conn:
            l = [ x[0] for x in sql_ask_database(conn, f"SELECT COUNT(index_chain) FROM (SELECT chain_id FROM chain JOIN structure ON chain.structure_id = structure.pdb_id WHERE rfam_acc='{f}' AND resolution <= {res_thr}) NATURAL JOIN nucleotide GROUP BY chain_id;", warn_every=0) ]
        lengths.append(l) # list of chain lengths from the family

        # notify(f"[{i+1}/{len(fam_list)}] Computed {f} chains lengths")

    # Plot the figure
    fig = plt.figure(figsize=(10,3))
    ax = fig.gca()
    ax.hist(lengths, bins=100, stacked=True, log=True, color=cols, label=fam_list)
    ax.set_xlabel("Sequence length (nucleotides)", fontsize=8)
    ax.set_ylabel("Number of 3D chains", fontsize=8)
    ax.set_xlim(left=-150)
    ax.tick_params(axis='both', which='both', labelsize=8)
    fig.tight_layout()

    # Draw the legend
    fig.subplots_adjust(right=0.78)
    filtered_handles = [mpatches.Patch(color='red'), mpatches.Patch(color='white'), mpatches.Patch(color='white'), mpatches.Patch(color='white'),
                        mpatches.Patch(color='blue'), mpatches.Patch(color='white'), mpatches.Patch(color='white'),
                        mpatches.Patch(color='green'), mpatches.Patch(color='white'),
                        mpatches.Patch(color='orange'), mpatches.Patch(color='white'),
                        mpatches.Patch(color='grey')]
    filtered_labels = ['Large Ribosomal Subunits', '(RF00002, RF02540,', 'RF02541, RF02543,', 'RF02546)',
                        'Small Ribosomal Subunits','(RF01960, RF00177,', 'RF02545)',
                       '5S rRNA', '(RF00001)', 
                       'tRNA', '(RF00005)', 
                       'Other']
    ax.legend(filtered_handles, filtered_labels, loc='right', 
                ncol=1, fontsize='small', bbox_to_anchor=(1.3, 0.5))

    # Save the figure
    fig.savefig(f"results/figures/lengths_{res_thr}A.png")
    idxQueue.put(thr_idx) # replace the thread index in the queue
    # notify("Computed sequence length statistics and saved the figure.")

def format_percentage(tot, x):
        if not tot:
            return '0 %'
        x = 100*x/tot
        if x >= 0.01:
            x = "%.2f" % x
        elif x == 0:
            return "0 %"
        else:
            x = "<.01"
        return x + '%'

def stats_freq():
    """Computes base frequencies in all RNA families.

    Outputs results/frequencies.csv
    REQUIRES tables chain, nucleotide up to date."""

    # Get a worker number to position the progress bar
    global idxQueue
    thr_idx = idxQueue.get()

    # Initialize a Counter object for each family
    freqs = {}
    for f in fam_list:
        freqs[f] = Counter()

    # List all nt_names happening within a RNA family and store the counts in the Counter
    for i,f in enumerate(tqdm(fam_list, position=thr_idx+1, desc=f"Worker {thr_idx+1}: Base frequencies", leave=False)):
        with sqlite3.connect("results/RNANet.db") as conn:
            counts = dict(sql_ask_database(conn, f"SELECT nt_name, COUNT(nt_name) FROM (SELECT chain_id from chain WHERE rfam_acc='{f}') NATURAL JOIN nucleotide GROUP BY nt_name;", warn_every=0))
        freqs[f].update(counts)
        # notify(f"[{i+1}/{len(fam_list)}] Computed {f} nucleotide frequencies.")
    
    # Create a pandas DataFrame, and save it to CSV.
    df = pd.DataFrame()
    for f in tqdm(fam_list, position=thr_idx+1, desc=f"Worker {thr_idx+1}: Base frequencies", leave=False):
        tot = sum(freqs[f].values())
        df = pd.concat([ df, pd.DataFrame([[ format_percentage(tot, x) for x in freqs[f].values() ]], columns=list(freqs[f]), index=[f]) ])
    df = df.fillna(0)
    df.to_csv("results/frequencies.csv")    
    idxQueue.put(thr_idx) # replace the thread index in the queue
    # notify("Saved nucleotide frequencies to CSV file.")

def parallel_stats_pairs(f):
    """Counts occurrences of intra-chain base-pair types in one RNA family

    REQUIRES tables chain, nucleotide up-to-date.""" 

    if path.isfile("data/"+f+"_pairs.csv") and path.isfile("data/"+f+"_counts.csv"):
        return

    # Get a worker number to position the progress bar
    global idxQueue
    thr_idx = idxQueue.get()

    chain_id_list = mappings_list[f]
    data = []
    sqldata = []
    for cid in tqdm(chain_id_list, position=thr_idx+1, desc=f"Worker {thr_idx+1}: {f} basepair types", leave=False):
        with sqlite3.connect("results/RNANet.db") as conn:
            # Get comma separated lists of basepairs per nucleotide
            interactions = pd.DataFrame(
                            sql_ask_database(conn, 
                                            f"SELECT nt_code as nt1, index_chain, paired, pair_type_LW FROM (SELECT chain_id FROM chain WHERE chain_id='{cid}') NATURAL JOIN nucleotide;",
                                            warn_every=0), 
                            columns = ["nt1", "index_chain", "paired", "pair_type_LW"]
                           )
        # expand the comma-separated lists in real lists
        expanded_list = pd.concat([ pd.DataFrame({  'nt1':[ row["nt1"] for x in row["paired"].split(',') ],
                                                    'index_chain':[ row['index_chain'] for x in row["paired"].split(',') ],
                                                    'paired':row['paired'].split(','), 
                                                    'pair_type_LW':row['pair_type_LW'].split(',') 
                                                }) 
                                    for _, row in interactions.iterrows() 
                                ]).reset_index(drop=True)

        # Add second nucleotide
        nt2 = []
        for _, row in expanded_list.iterrows():
            if row.paired in ['', '0']:
                nt2.append('')
            else:
                try:
                    n = expanded_list[expanded_list.index_chain == int(row.paired)].nt1.tolist()[0]
                    nt2.append(n)
                except IndexError:
                    print(cid, flush=True)
        try:
            expanded_list["nt2"] = nt2
        except ValueError:
            print(cid, flush=True)
            print(expanded_list, flush=True)
            return 0,0

        # keep only intra-chain interactions
        expanded_list = expanded_list[ ~expanded_list.paired.isin(['0','']) ]
        expanded_list["nts"] = expanded_list["nt1"] + expanded_list["nt2"]
        
        # Get basepair type
        expanded_list["basepair"] = np.where(expanded_list.nts.isin(["AU","UA"]), "AU",
                                        np.where(expanded_list.nts.isin(["GC","CG"]), "GC",
                                            np.where(expanded_list.nts.isin(["GU","UG"]), "Wobble","Other")
                                        )
                                    )
        expanded_list = expanded_list[["basepair", "pair_type_LW"]]

        # Update the database
        vlcnts = expanded_list.pair_type_LW.value_counts()
        sqldata.append(   ( vlcnts.at["cWW"]/2 if "cWW" in vlcnts.index else 0, 
                            vlcnts.at["cWH"] if "cWH" in vlcnts.index else 0, 
                            vlcnts.at["cWS"] if "cWS" in vlcnts.index else 0, 
                            vlcnts.at["cHH"]/2 if "cHH" in vlcnts.index else 0, 
                            vlcnts.at["cHS"] if "cHS" in vlcnts.index else 0, 
                            vlcnts.at["cSS"]/2 if "cSS" in vlcnts.index else 0, 
                            vlcnts.at["tWW"]/2 if "tWW" in vlcnts.index else 0, 
                            vlcnts.at["tWH"] if "tWH" in vlcnts.index else 0, 
                            vlcnts.at["tWS"] if "tWS" in vlcnts.index else 0, 
                            vlcnts.at["tHH"]/2 if "tHH" in vlcnts.index else 0, 
                            vlcnts.at["tHS"] if "tHS" in vlcnts.index else 0, 
                            vlcnts.at["tSS"]/2 if "tSS" in vlcnts.index else 0, 
                            int(sum(vlcnts.loc[[ str(x) for x in vlcnts.index if "." in str(x)]])/2), 
                            cid) )

        data.append(expanded_list)

    # Update the database
    with sqlite3.connect("results/RNANet.db", isolation_level=None) as conn:
        conn.execute('pragma journal_mode=wal') # Allow multiple other readers to ask things while we execute this writing query
        sql_execute(conn, """UPDATE chain SET pair_count_cWW = ?, pair_count_cWH = ?, pair_count_cWS = ?, pair_count_cHH = ?,
                                pair_count_cHS = ?, pair_count_cSS = ?, pair_count_tWW = ?, pair_count_tWH = ?, pair_count_tWS = ?, 
                                pair_count_tHH = ?, pair_count_tHS = ?, pair_count_tSS = ?, pair_count_other = ? WHERE chain_id = ?;""", many=True, data=sqldata, warn_every=0)

    # merge all the dataframes from all chains of the family
    expanded_list = pd.concat(data)

    # Count each pair type
    vcnts = expanded_list.pair_type_LW.value_counts()

    # Add these new counts to the family's counter
    cnt = Counter()
    cnt.update(dict(vcnts))

    # Create an output DataFrame
    f_df = pd.DataFrame([[ x for x in cnt.values() ]], columns=list(cnt), index=[f])
    f_df.to_csv(f"data/{f}_counts.csv")
    expanded_list.to_csv(f"data/{f}_pairs.csv")
    
    idxQueue.put(thr_idx) # replace the thread index in the queue

def to_dist_matrix(f):
    if path.isfile("data/"+f+".npy"):
        # notify(f"Computed {f} distance matrix", "loaded from file")
        return 0
  
    # Get a worker number to position the progress bar
    global idxQueue
    thr_idx = idxQueue.get()

    # notify(f"Computing {f} distance matrix from alignment...")
    command = f"esl-alipid --rna --noheader --informat stockholm {f}_3d_only.stk"

    # Prepare a file
    with open(path_to_seq_data+f"/realigned/{f}++.afa") as al_file:
        al = AlignIO.read(al_file, "fasta")
        names = [ x.id for x in al if '[' in x.id ]
        al = al[-len(names):]
    with open(f + "_3d_only.stk", "w") as only_3d:
        only_3d.write(al.format("stockholm"))
    del al

    # Prepare the job
    process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE)
    id_matrix = np.zeros((len(names), len(names)))

    pbar = tqdm(total = len(names)*(len(names)-1)*0.5, position=thr_idx+1, desc=f"Worker {thr_idx+1}: {f} idty matrix", leave=False)
    while process.poll() is None:
        output = process.stdout.readline()
        if output:
            lines = output.strip().split(b'\n')
            for l in lines:
                line = l.split()
                s1 = line[0].decode('utf-8')
                s2 = line[1].decode('utf-8')
                score = line[2].decode('utf-8')
                id1 = names.index(s1)
                id2 = names.index(s2)
                id_matrix[id1, id2] = float(score)
                pbar.update(1)
    pbar.close()

    subprocess.run(["rm", "-f", f + "_3d_only.stk"])
    np.save("data/"+f+".npy", id_matrix)
    idxQueue.put(thr_idx) # replace the thread index in the queue
    return 0

def seq_idty():
    """Computes identity matrices for each of the RNA families.
    
    REQUIRES temporary results files in data/*.npy
    REQUIRES tables chain, family un to date."""

    # load distance matrices
    fam_arrays = []
    for f in famlist:
        if path.isfile("data/"+f+".npy"):
            fam_arrays.append(np.load("data/"+f+".npy"))
        else:
            fam_arrays.append([])

    # Update database with identity percentages
    conn = sqlite3.connect("results/RNANet.db")
    for f, D in zip(famlist, fam_arrays):
        if not len(D): continue
        a = 1.0 - np.average(D + D.T) # Get symmetric matrix instead of lower triangle + convert from distance matrix to identity matrix
        conn.execute(f"UPDATE family SET idty_percent = {round(float(a),2)} WHERE rfam_acc = '{f}';")
    conn.commit()
    conn.close()

    # Plots plots plots
    fig, axs = plt.subplots(4,17, figsize=(17,5.75))
    axs = axs.ravel()
    [axi.set_axis_off() for axi in axs]
    im = "" # Just to declare the variable, it will be set in the loop
    for f, D, ax in zip(famlist, fam_arrays, axs):
        if not len(D): continue
        if D.shape[0] > 2:  # Cluster only if there is more than 2 sequences to organize
            D = D + D.T     # Copy the lower triangle to upper, to get a symetrical matrix
            condensedD = squareform(D)

            # Compute basic dendrogram by Ward's method
            Y = sch.linkage(condensedD, method='ward')
            Z = sch.dendrogram(Y, orientation='left', no_plot=True)

            # Reorganize rows and cols
            idx1 = Z['leaves']
            D = D[idx1,:]
            D = D[:,idx1[::-1]]
        im = ax.matshow(1.0 - D, vmin=0, vmax=1, origin='lower') # convert to identity matrix 1 - D from distance matrix D
        ax.set_title(f + "\n(" + str(len(mappings_list[f]))+ " chains)", fontsize=10)
    fig.tight_layout()
    fig.subplots_adjust(wspace=0.1, hspace=0.3)
    fig.colorbar(im, ax=axs[-1], shrink=0.8)
    fig.savefig(f"results/figures/distances.png")
    notify("Computed all identity matrices and saved the figure.")

def stats_pairs():
    """Counts occurrences of intra-chain base-pair types in RNA families

    Creates a temporary results file in data/pair_counts.csv, and a results file in results/pairings.csv.
    REQUIRES tables chain, nucleotide up-to-date.""" 
    
    def line_format(family_data):
        return family_data.apply(partial(format_percentage, sum(family_data)))

    if not path.isfile("data/pair_counts.csv"):
        results = []
        allpairs = []
        for f in fam_list:
            newpairs = pd.read_csv(f"data/{f}_pairs.csv", index_col=0)
            fam_df = pd.read_csv(f"data/{f}_counts.csv", index_col=0)
            results.append(fam_df)
            allpairs.append(newpairs)
            subprocess.run(["rm", "-f", f"data/{f}_pairs.csv"])
            subprocess.run(["rm", "-f", f"data/{f}_counts.csv"])
        all_pairs = pd.concat(allpairs)
        df = pd.concat(results).fillna(0)
        df.to_csv("data/pair_counts.csv")
        all_pairs.to_csv("data/all_pairs.csv")
    else:
        df = pd.read_csv("data/pair_counts.csv", index_col=0)
        all_pairs = pd.read_csv("data/all_pairs.csv", index_col=0)

    crosstab = pd.crosstab(all_pairs.pair_type_LW, all_pairs.basepair)
    col_list = [ x for x in df.columns if '.' in x ]

    # Remove not very well defined pair types (not in the 12 LW types)
    df['other'] = df[col_list].sum(axis=1)
    df.drop(col_list, axis=1, inplace=True)
    crosstab = crosstab.append(crosstab.loc[col_list].sum(axis=0).rename("non-LW"))
    
    # drop duplicate types
    # The twelve Leontis-Westhof types are
    # cWW cWH cWS cHH cHS cSS (do not count cHW cSW and cSH, they are the same as their opposites)
    # tWW tWH tWS tHH tHS tSS (do not count tHW tSW and tSH, they are the same as their opposites)
    df = df.drop([ x for x in [ "cHW", "tHW", "cSW", "tSW", "cHS", "tHS"] if x in df.columns], axis=1)
    crosstab = crosstab.loc[[ x for x in ["cWW","cWH","cWS","cHH","cHS","cSS","tWW","tWH","tWS","tHH","tHS","tSS","non-LW"] if x in crosstab.index]]
    df.loc[:,[x for x in ["cWW", "tWW", "cHH", "tHH", "cSS", "tSS", "other"] if x in df.columns] ] /= 2
    crosstab.loc[["cWW", "tWW", "cHH", "tHH", "cSS", "tSS", "non-LW"]] /= 2

    # Compute total row
    total_series = df.sum(numeric_only=True).rename("TOTAL")
    df = df.append(total_series)

    # format as percentages
    df = df.apply(line_format, axis=1)

    # reorder columns
    df.sort_values("TOTAL", axis=1, inplace=True, ascending=False)
    crosstab = crosstab[["AU", "GC", "Wobble", "Other"]]

    # Save to CSV
    df.to_csv("results/pair_types.csv")

    # Plot barplot of overall types
    ax = crosstab.plot(figsize=(8,5), kind='bar', stacked=True, log=False, fontsize=13)
    ax.set_ylabel("Number of observations (millions)", fontsize=13)
    ax.set_xlabel(None)
    plt.subplots_adjust(left=0.1, bottom=0.16, top=0.95, right=0.99)
    plt.savefig("results/figures/pairings.png")

    notify("Computed nucleotide statistics and saved CSV and PNG file.")

def per_chain_stats():
    """Computes per-chain frequencies and base-pair type counts.

    REQUIRES tables chain, nucleotide up to date. """

    with sqlite3.connect("results/RNANet.db", isolation_level=None) as conn:
        # Compute per-chain nucleotide frequencies
        df = pd.read_sql("SELECT SUM(is_A) as A, SUM(is_C) AS C, SUM(is_G) AS G, SUM(is_U) AS U, SUM(is_other) AS O, chain_id FROM nucleotide GROUP BY chain_id;", conn)
        df["total"] = pd.Series(df.A + df.C + df.G + df.U + df.O, dtype=np.float64)
        df[['A','C','G','U','O']] = df[['A','C','G','U','O']].div(df.total, axis=0)
        df = df.drop("total", axis=1)

        # Set the values
        conn.execute('pragma journal_mode=wal')
        sql_execute(conn, "UPDATE chain SET chain_freq_A = ?, chain_freq_C = ?, chain_freq_G = ?, chain_freq_U = ?, chain_freq_other = ? WHERE chain_id= ?;",
                          many=True, data=list(df.to_records(index=False)), warn_every=10)
    notify("Updated the database with per-chain base frequencies")

def log_to_pbar(pbar):
    def update(r):
        pbar.update(1)
    return update

if __name__ == "__main__":

    # parse options
    try:
        opts, _ = getopt.getopt( sys.argv[1:], "r:h", [ "help", "resolution=", "3d-folder=", "seq-folder=" ])
    except getopt.GetoptError as err:
        print(err)
        sys.exit(2)
    for opt, arg in opts:

        if opt == "-h" or opt == "--help":
            print(  "RNANet statistics, a script to build a multiscale RNA dataset from public data\n"
                    "Developped by Louis Becquey (louis.becquey@univ-evry.fr), 2020")
            print()
            print("Options:")
            print("-h [ --help ]\t\t\tPrint this help message")
            print()
            print("-r 20.0 [ --resolution=20.0 ]\tCompute statistics using chains of resolution 20.0A or better.")
            print("--3d-folder=…\t\t\tPath to a folder containing the 3D data files. Required subfolders should be:"
                    "\n\t\t\t\t\tdatapoints/\t\tFinal results in CSV file format.")
            print("--seq-folder=…\t\t\tPath to a folder containing the sequence and alignment files. Required subfolder:"
                    "\n\t\t\t\t\trealigned/\t\tSequences, covariance models, and alignments by family")
            sys.exit()
        elif opt == '--version':
            print("RNANet statistics 1.1 beta")
            sys.exit()
        elif opt == "-r" or opt == "--resolution":
            assert float(arg) > 0.0 and float(arg) <= 20.0 
            res_thr = float(arg)
        elif opt=='--3d-folder':
            path_to_3D_data = path.abspath(arg)
            if path_to_3D_data[-1] != '/':
                path_to_3D_data += '/'
        elif opt=='--seq-folder':
            path_to_seq_data = path.abspath(arg)
            if path_to_seq_data[-1] != '/':
                path_to_seq_data += '/'
    

    # Load mappings
    print("Loading mappings list...")
    with sqlite3.connect("results/RNANet.db") as conn:
        fam_list = [ x[0] for x in sql_ask_database(conn, "SELECT rfam_acc from family ORDER BY rfam_acc ASC;") ]
        mappings_list = {}
        for k in fam_list:
            mappings_list[k] = [ x[0] for x in sql_ask_database(conn, f"SELECT chain_id from chain WHERE rfam_acc='{k}' and issue=0;") ]

    # List the families for which we will compute sequence identity matrices
    with sqlite3.connect("results/RNANet.db") as conn:
        famlist = [ x[0] for x in sql_ask_database(conn, "SELECT rfam_acc from (SELECT rfam_acc, COUNT(chain_id) as n_chains FROM family NATURAL JOIN chain GROUP BY rfam_acc) WHERE n_chains > 0 ORDER BY rfam_acc ASC;") ]
        ignored = [ x[0] for x in sql_ask_database(conn, "SELECT rfam_acc from (SELECT rfam_acc, COUNT(chain_id) as n_chains FROM family NATURAL JOIN chain GROUP BY rfam_acc) WHERE n_chains < 2 ORDER BY rfam_acc ASC;") ]
    if len(ignored):
        print(f"Idty matrices: Ignoring {len(ignored)} families with only one chain:", " ".join(ignored)+'\n')
    
    # Prepare the multiprocessing execution environment
    nworkers = max(read_cpu_number()-1, 32)
    thr_idx_mgr = Manager()
    idxQueue = thr_idx_mgr.Queue()
    for i in range(nworkers):
        idxQueue.put(i)

    # Define the tasks
    joblist = []
    # joblist.append(Job(function=reproduce_wadley_results, args=(1,)))
    # joblist.append(Job(function=reproduce_wadley_results, args=(4,)))
    joblist.append(Job(function=stats_len)) # Computes figures
    # joblist.append(Job(function=stats_freq)) # updates the database
    # for f in famlist:
    #     joblist.append(Job(function=parallel_stats_pairs, args=(f,))) # updates the database
    #     if f not in ignored:
    #         joblist.append(Job(function=to_dist_matrix, args=(f,))) # updates the database

    p = Pool(initializer=init_worker, initargs=(tqdm.get_lock(),), processes=nworkers)
    pbar = tqdm(total=len(joblist), desc="Stat jobs", position=0, leave=True)

    try:
        for j in joblist:
            p.apply_async(j.func_, args=j.args_, callback=log_to_pbar(pbar))
        p.close()
        p.join()
        pbar.close()
    except KeyboardInterrupt:
        warn("KeyboardInterrupt, terminating workers.", error=True)
        p.terminate()
        p.join()
        pbar.close()
        exit(1)
    except:
        print("Something went wrong")

    print()
    print()

    # finish the work after the parallel portions
    # per_chain_stats()
    # seq_idty()
    # stats_pairs()
